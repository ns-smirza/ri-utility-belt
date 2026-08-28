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

  # --- pod rollout history (last 2 revisions) per tracked deployment ---
  # Derive each deployment name from its pod name (strip the ReplicaSet + pod
  # hash suffix), look up that deployment's image-base from the pod->image map,
  # and run `kubectl rollout history` to capture the two highest revisions
  # (current = highest, previous = second-highest). Only RI images are emitted,
  # matching the IMG filter above, so the JSON renderer can join on image-base.
  grep -E "artifactservice|artifactsync|vpe-manager|callhome|alarmmanager|cloudmetricsgenerator|diagnostic" "$out.pods" 2>/dev/null | \
    grep -v "deprovision" | \
    awk '{print $1}' | \
    sed -E 's/-[0-9a-f]{8,12}-[0-9a-z]{4,6}$//' | \
    sort -u | \
    while read -r dep; do
      [ -n "$dep" ] || continue
      # A pod may have several containers (2/2); the map field is space-joined
      # images, so split on spaces and pick the RI one — same logic as the IMG
      # loop above — then strip to image-base (no registry path, no :tag).
      imgbase=$(awk -v d="$dep-" -F '\t' 'index($1,d)==1 {print $2; exit}' "$out.map" 2>/dev/null | tr ' ' '\n' | grep -E "risk-insights-(production|release|develop)-docker" | sed 's#.*/##; s/:.*//' | head -1)
      [ -n "$imgbase" ] || continue
      revs=$(KUBECONFIG="$kube" kubectl --request-timeout="$GET_TIMEOUT" rollout history deployment/"$dep" -n risk-insights 2>/dev/null | awk '$1 ~ /^[0-9]+$/ {print $1}' | sort -n | tail -2)
      cur=$(printf '%s\n' "$revs" | tail -1)
      prev=$(printf '%s\n' "$revs" | sed -n '1p')
      if [ -n "$cur" ] && [ "$cur" != "$prev" ]; then
        printf "ROLL|%s|%s|%s\n" "$imgbase" "$cur" "$prev"
      elif [ -n "$cur" ]; then
        printf "ROLL|%s|%s|\n" "$imgbase" "$cur"
      fi
    done > "$out.roll"

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
    [ -f "$out.roll" ] && cat "$out.roll"
    [ -f "$out.pkg" ] && cat "$out.pkg"
  } > "$out"
  rm -f "$out.img" "$out.roll" "$out.pkg" "$out.pods" "$out.map"
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
        | (if $r[0]=="ROLL" and .cur != null then .recs += [[.cur, "ROLL", $r[1], $r[2], $r[3]]] else . end)
        | (if $r[0]=="PKG" and .cur != null then .recs += [[.cur, "PKG", $r[1], $r[2]]] else . end))
    | .recs
    | sort_by(.[0])
    | group_by(.[0])
    | map({
        name: .[0][0],
        images: (
          (map(select(.[1]=="ROLL"))
            | map({(.[2]): {current: (.[3] | tonumber), previous: (try (.[4] | tonumber) catch null)}})
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
