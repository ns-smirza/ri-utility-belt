set +m
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

# --- args: --prod (everything except npe), --npe (only npe stacks),
#     --json (emit JSON instead of the text matrix; orthogonal to --prod/--npe), none = all ---
mode=""
json=0
for arg in "$@"; do
  case "$arg" in
    --prod) mode=prod ;;
    --npe)  mode=npe  ;;
    --json) json=1 ;;
    *) echo "Unknown argument: $arg (expected --prod, --npe, or --json)" >&2 ;;
  esac
done

is_npe() { case "$1" in *qa01*|*stg01*|*devint*|*npe02*|*fed1mp*|*perf01*) return 0;; *) return 1;; esac }

# Per-call kubectl request timeouts so one slow/unreachable cluster can't stall the gather.
GET_TIMEOUT=${GET_TIMEOUT:-15}
EXEC_TIMEOUT=${EXEC_TIMEOUT:-30}

for kube in *.yaml; do
  case "$mode" in
    npe)  is_npe "$kube" || continue ;;
    prod) is_npe "$kube" && continue ;;
  esac
(
  safe=$(printf '%s' "$kube" | tr '/' '_')
  out="$tmpdir/$safe.data"

  # --- pods: one table call (name+status) + one jsonpath call (name->images) ---
  # Two bounded calls replace the former per-pod `get pod` loop, so one slow pod
  # can no longer stall the whole stack's gather.
  KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" get pods -n risk-insights --no-headers 2>/dev/null > "$out.pods"
  KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" get pods -n risk-insights -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].image}{"\n"}{end}' 2>/dev/null > "$out.map"

  # --- images (with pod name + status for the dashboard's running indicator) ---
  grep -E "artifactservice|artifactsync|vpe-manager|callhome|alarmmanager|cloudmetricsgenerator|diagnostic" "$out.pods" 2>/dev/null | \
    grep -v "deprovision" | \
    awk '{print $1, $3}' | \
    while read -r p status; do
      awk -v p="$p" -F '\t' '$1 == p {print $2}' "$out.map" 2>/dev/null | \
        tr ' ' '\n' | \
        grep -E "risk-insights-(production|release|develop)-docker" | \
        sed 's#.*/##' | \
        while read -r img; do
          [ -n "$img" ] && printf "IMG|%s|%s|%s\n" "$img" "$p" "$status"
        done
    done > "$out.img"

  # --- deployment age (current rollout) per tracked deployment ---
  # The active ReplicaSet's creationTimestamp is when the currently-running
  # pod template was rolled out. It is stable across pod restarts — a
  # crashloop restart resets pod age, but not the ReplicaSet age — so it is
  # the right signal for "how old is the current deployment". Query every RS
  # in one call, keep the active one (status.replicas > 0; the newest ts if a
  # rollout-in-progress has two live), strip the RS hash to get the deployment
  # name, and look up that deployment's image-base from the pod->image map
  # (multi-container pods: split the space-joined field and pick the RI image,
  # same as the IMG loop above). Emit the creationTimestamp keyed by image-base
  # so the JSON renderer can join on image-base.
  KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" get rs -n risk-insights -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.creationTimestamp}{"\t"}{.status.replicas}{"\n"}{end}' 2>/dev/null > "$out.rs"
  awk -F '\t' '
    $3 != "0" && $1 !~ /deprovision/ && $1 ~ /artifactservice|artifactsync|vpe-manager|callhome|alarmmanager|cloudmetricsgenerator|diagnostic/ {
      sub(/-[0-9a-z]{8,12}$/, "", $1)
      if (!($1 in ts) || $2 > ts[$1]) ts[$1] = $2
    }
    END { for (d in ts) print d "\t" ts[d] }
  ' "$out.rs" 2>/dev/null | \
    while IFS=$'\t' read -r dep tstamp; do
      [ -n "$dep" ] || continue
      imgbase=$(awk -v d="$dep-" -F '\t' 'index($1,d)==1 {print $2; exit}' "$out.map" 2>/dev/null | tr ' ' '\n' | grep -E "risk-insights-(production|release|develop)-docker" | sed 's#.*/##; s/:.*//' | head -1)
      [ -n "$imgbase" ] || continue
      printf "ROLL|%s|%s\n" "$imgbase" "$tstamp"
    done > "$out.roll"
  rm -f "$out.rs"

  # --- MP-side services: callhomeservice / logwatcher / logcollector ---------
  # These live in per-stack namespaces whose SUFFIX is stable
  # ("--callhomeservice" / "--logwatcher" / "--logcollector"; the prefix varies:
  # mp-fed1mp--, stg01-mp--, lon3-mp-prod--), not in risk-insights. Every
  # sub-deployment of a service shares ONE image (callhome / nslogcollector /
  # logwatcher), so each service yields one dashboard row. Namespaces are
  # discovered by suffix and queried ONE AT A TIME — cluster-wide `-A` pod/RS
  # listings exceed GET_TIMEOUT on these large MP clusters (observed: first
  # `-A` call returns 0 lines). Filtering by namespace (not pod name) also
  # excludes the RI-side ri-logwatcher pods in risk-insights.
  KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" get ns --no-headers 2>/dev/null | \
    awk '$1 ~ /--(callhomeservice|logwatcher|logcollector)$/ {print $1}' > "$out.mns"

  : > "$out.mimg"
  : > "$out.mroll"
  while read -r mns; do
    [ -n "$mns" ] || continue
    # ONE call per namespace carrying name + status + images. Status comes from
    # status.phase, upgraded to the first waiting reason (CrashLoopBackOff etc.)
    # when present. (A separate --no-headers table call proved flaky here: a
    # cold first list in a namespace can exceed GET_TIMEOUT while the follow-up
    # call succeeds — observed on lon3's logwatcher ns — so don't split this.)
    KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" get pods -n "$mns" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\t"}{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}{"\t"}{.spec.containers[*].image}{"\n"}{end}' 2>/dev/null | \
      awk -F '\t' '{ st = $3; sub(/ .*/, "", st); if (st == "") st = $2; print $1 "\t" st "\t" $4 }' > "$out.mpods"

    while IFS=$'\t' read -r mpod mstatus mimages; do
      [ -n "$mpod" ] || continue
      printf '%s\n' "$mimages" | \
        tr ' ' '\n' | \
        sed 's#.*/##' | \
        grep -E "^(callhome|nslogcollector|logwatcher):" | \
        sort -u | \
        while read -r img; do
          [ -n "$img" ] && printf "IMG|%s|%s|%s\n" "$img" "$mpod" "$mstatus"
        done
    done < "$out.mpods" >> "$out.mimg"

    # Deployment age: newest active ReplicaSet per service image. A service's
    # sub-deployments (e.g. logcollector-fastforward/-segmenter) can roll at
    # different times; the row shows the NEWEST rollout age across them, keyed
    # by shared image-base so the JSON renderer joins it like the RI rows.
    KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" get rs -n "$mns" -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.creationTimestamp}{"\t"}{.status.replicas}{"\n"}{end}' 2>/dev/null | \
      awk -F '\t' '$3 != "0" && $3 != "" {print $1 "\t" $2}' | \
      while IFS=$'\t' read -r rsname rts; do
        [ -n "$rsname" ] || continue
        dep=$(printf '%s' "$rsname" | sed 's/-[0-9a-z]\{8,12\}$//')
        imgbase=$(awk -v d="$dep-" -F '\t' 'index($1,d)==1 {print $3; exit}' "$out.mpods" 2>/dev/null | tr ' ' '\n' | sed 's#.*/##; s/:.*//' | grep -E "^(callhome|nslogcollector|logwatcher)$" | head -1)
        [ -n "$imgbase" ] || continue
        printf "%s\t%s\n" "$imgbase" "$rts"
      done | \
      awk -F '\t' '{ if (!($1 in ts) || $2 > ts[$1]) ts[$1] = $2 } END { for (b in ts) printf "ROLL|%s|%s\n", b, ts[b] }' >> "$out.mroll"
  done < "$out.mns"

  # --- internal packages, per category, newest-first (GNU sort -V inside the pod) ---
  art_pod=$(awk '$3 == "Running" && $1 ~ /^artifactservice-/ {print $1; exit}' "$out.pods" 2>/dev/null)

  if [ -n "$art_pod" ]; then
    KUBECONFIG="$kube" kubectl --request-timeout="$EXEC_TIMEOUT" exec -n risk-insights "$art_pod" -- bash -c '
      entries=(
        "vsp-ais|/opt/ns/downloads/vsp-ais/"
        "vsp-said|/opt/ns/downloads/vsp-said/"
        "vsp-swg|/opt/ns/downloads/vsp-swg/"
        "vpe-content|/opt/ns/downloads/vpe-content/"
        "vpe-geoipdb|/opt/ns/downloads/vpe-geoipdb/"
        "vpe-sf|/opt/ns/downloads/vpe-sf/"
        "kvm|/opt/ns/downloads/vpe-images/kvm/"
        "ova|/opt/ns/downloads/vpe-images/ova/"
      )
      for entry in "${entries[@]}"; do
        catname="${entry%%|*}"
        path="${entry#*|}"
        if [ -d "$path" ]; then
          ls "$path" 2>/dev/null | sort -V -r | while read -r file; do
            [ -n "$file" ] && printf "PKG|%s|%s\n" "$catname" "$file"
          done
        fi
      done
    ' 2>/dev/null > "$out.pkg"
  fi

  # --- combined record for this stack ---
  {
    echo "STACK|$kube"
    cat "$out.img"
    [ -f "$out.mimg" ] && cat "$out.mimg"
    [ -f "$out.roll" ] && cat "$out.roll"
    [ -f "$out.mroll" ] && cat "$out.mroll"
    [ -f "$out.pkg" ] && cat "$out.pkg"
  } > "$out"
  rm -f "$out.img" "$out.roll" "$out.pkg" "$out.pods" "$out.map" "$out.mns" "$out.mpods" "$out.mmap" "$out.mimg" "$out.mroll"
) &
done
wait
set -m

# --- render ---
if [ "$json" -eq 1 ]; then
  # JSON output: collect (stack,type,...) tuples preserving order, then group by stack.
  # Empty stacks (no images/packages) are dropped, consistent with the table renderer.
  cat "$tmpdir"/*.data 2>/dev/null | jq -Rn '
    [inputs | split("|")] as $rows
    | reduce $rows[] as $r ({cur:null, recs:[]};
        (if $r[0]=="STACK" then .cur = $r[1] else . end)
        | (if $r[0]=="IMG" and .cur != null then .recs += [[.cur, "IMG", $r[1], $r[2], $r[3]]] else . end)
        | (if $r[0]=="ROLL" and .cur != null then .recs += [[.cur, "ROLL", $r[1], $r[2]]] else . end)
        | (if $r[0]=="PKG" and .cur != null then .recs += [[.cur, "PKG", $r[1], $r[2]]] else . end))
    | .recs
    | sort_by(.[0])
    | group_by(.[0])
    | map({
        name: .[0][0],
        images: (
          (map(select(.[1]=="ROLL"))
            | map({(.[2]): {created: .[3]}})
            | add // {}) as $roll
          |
          map(select(.[1]=="IMG") | .[2:])
          | group_by(.[0] | split(":")[0])
          | map(. as $g | (any($g[]; .[2]=="Running")) as $r | if $r then map(select(.[2]=="Running")) else . end)
          | (add // [])
          | group_by(.[0])
          | map({
              image: .[0][0],
              running: (map(.[2] == "Running") | all),
              status: ([.[] | .[2]] | unique | join(", ")),
              pods: (map({name: .[1], status: .[2]})),
              rollout: ($roll[.[0][0] | split(":")[0]] // null)
            })
        ),
        packages: (reduce .[] as $r ({}; if $r[1]=="PKG" then .[$r[2]] += [$r[3]] else . end))
      })
    | {stacks: .}
  '
  exit
fi

# --- render matrix ---
cat "$tmpdir"/*.data 2>/dev/null | awk -F'|' '
BEGIN {
  colname[1]="Stack"; colname[2]="Images"; colname[3]="vsp-ais";
  colname[4]="vsp-said"; colname[5]="vsp-swg"; colname[6]="vpe-content";
  colname[7]="vpe-geoipdb"; colname[8]="vpe-sf"; colname[9]="kvm"; colname[10]="ova";
  ncols=10
  colidx["Images"]=2; colidx["vsp-ais"]=3; colidx["vsp-said"]=4; colidx["vsp-swg"]=5;
  colidx["vpe-content"]=6; colidx["vpe-geoipdb"]=7; colidx["vpe-sf"]=8;
  colidx["kvm"]=9; colidx["ova"]=10;
}
{
  t=$1
  if (t=="STACK") { ns++; stacks[ns]=$2; cell[ns,1,1]=$2; nlines[ns,1]=1 }
  else if (t=="IMG" && $4=="Running")  { c=2; img=$2; if (!(seenimg[ns,img]++)) { k=++nlines[ns,c]; cell[ns,c,k]=img } }
  else if (t=="PKG")  { c=colidx[$2]; if (!c) next; k=++nlines[ns,c]; cell[ns,c,k]=$3 }
}
END {
  if (ns==0) exit
  for (s=1; s<=ns; s++) {
    tot=0; for (c=2; c<=ncols; c++) tot+=nlines[s,c]; if (tot>0) keep[s]=1
  }
  for (c=1; c<=ncols; c++) {
    w=length(colname[c])
    for (s=1; s<=ns; s++) if (keep[s]) for (k=1; k<=nlines[s,c]; k++) {
      l=length(cell[s,c,k]); if (l>w) w=l
    }
    width[c]=w
  }
  for (s=1; s<=ns; s++) if (keep[s]) {
    h=1; for (c=1; c<=ncols; c++) if (nlines[s,c]>h) h=nlines[s,c]; height[s]=h
  }
  # header
  printf "%s", pad(colname[1],width[1])
  for (c=2; c<=ncols; c++) printf " | %s", pad(colname[c],width[c])
  printf "\n"
  # separator
  printf "%s", dash(width[1])
  for (c=2; c<=ncols; c++) printf "-+--%s", dash(width[c])
  printf "\n"
  # rows (multi-line cells)
  for (s=1; s<=ns; s++) {
    if (!keep[s]) continue
    for (k=1; k<=height[s]; k++) {
      printf "%s", pad(getcell(s,1,k),width[1])
      for (c=2; c<=ncols; c++) printf " | %s", pad(getcell(s,c,k),width[c])
      printf "\n"
    }
    printf "%s", dash(width[1])
    for (c=2; c<=ncols; c++) printf "-+--%s", dash(width[c])
    printf "\n"
  }
}
function pad(s,w, i,r){ r=s; for(i=length(s); i<w; i++) r=r" "; return r }
function dash(w, i,r){ r=""; for(i=0; i<w; i++) r=r"-"; return r }
function getcell(s,c,k){ return (k<=nlines[s,c]) ? cell[s,c,k] : "" }
'
