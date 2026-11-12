# Sourced by the stubs in bin/ (bash 3.2 compatible). json STRING prints STRING as a JSON string literal,
# every control character escaped.
json() {
  local s="$1" i ch esc hex
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"
  [[ $s == *[[:cntrl:]]* ]] || { printf '"%s"' "$s"; return; }
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31; do
    printf -v hex %02x "$i"; printf -v ch "\\x$hex"
    printf -v esc '\\u%04x' "$i"
    s="${s//"$ch"/$esc}"
  done
  printf '"%s"' "$s"
}

# record CMD [ENV_REGEX]: append one line {"cmd":CMD,"args":[...],"env":{...}} to $STUB_LOG, the
# exported variables whose names match ENV_REGEX in "env". Called as `record cmd regex "$@"`.
record() {
  local cmd="$1" re="$2" line sep="" a k
  shift 2
  [[ -n ${STUB_LOG:-} ]] || return 0
  line="{\"cmd\":$(json "$cmd"),\"args\":["
  for a in "$@"; do line+="$sep$(json "$a")"; sep=,; done
  line+='],"env":{' sep=""
  if [[ -n $re ]]; then
    for k in $(compgen -e); do
      [[ $k =~ $re ]] || continue
      line+="$sep$(json "$k"):$(json "${!k}")"; sep=,
    done
  fi
  echo "$line}}" >>"$STUB_LOG"
}
