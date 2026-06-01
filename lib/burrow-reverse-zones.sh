#!/usr/bin/env bash
# Burrow — reverse-zone helper.
#
# Derives the in-addr.arpa reverse zone NAME(S) for a LAN CIDR, kept in lockstep
# with burrow-pdns-bootstrap.sh (which creates them in PowerDNS). Reverse zones
# in PowerDNS are per-/24, so a CIDR is covered by the SET of /24 zones spanning
# it — not one over-broad classful zone (a /23 must NOT collapse to a /16 zone,
# which would shadow real PTR recursion for the rest of 192.168.x):
#   prefix >= 24       -> one /24 zone   (192.168.1.0/24 -> 1.168.192.in-addr.arpa)
#   20 <= prefix < 24  -> 2..16 /24 zones spanning the block (192.168.0.0/23 ->
#                         0.168.192 + 1.168.192.in-addr.arpa)
#   prefix < 20        -> too wide to auto-create per-/24 zones; emit none + warn
#                         (create specific reverse zones from the dashboard instead)
#
# Usage:
#   source lib/burrow-reverse-zones.sh
#   burrow_reverse_zone_names 192.168.0.0/23   # one zone name per line
#   burrow_reverse_zones "$LAN_CIDR"          # unbound stub-zone block(s)

# Emit one reverse-zone name per line covering a single CIDR. rc=1 (no output) if
# the prefix is too wide to enumerate as /24s.
burrow_reverse_zone_names() {
    local cidr="$1" ip pfx o1 o2 o3 count base i
    ip="${cidr%/*}"; pfx="${cidr#*/}"
    IFS=. read -r o1 o2 o3 _ <<<"$ip"
    if   [ "${pfx:-0}" -ge 24 ]; then
        echo "${o3}.${o2}.${o1}.in-addr.arpa"
    elif [ "${pfx:-0}" -ge 20 ]; then
        count=$(( 1 << (24 - pfx) ))          # number of /24s in the block
        base=$(( o3 - (o3 % count) ))         # align to the block boundary
        for (( i = 0; i < count; i++ )); do
            echo "$(( base + i )).${o2}.${o1}.in-addr.arpa"
        done
    else
        return 1
    fi
}

# Emit an Unbound stub-zone block per reverse zone covering the CIDR(s).
# Accepts one CIDR or a comma/space-separated list.
burrow_reverse_zones() {
    local cidr name
    for cidr in ${1//,/ }; do
        while IFS= read -r name; do
            [ -n "$name" ] && printf 'stub-zone:\n    name: "%s"\n    stub-addr: 127.0.0.1@5300\n    stub-prime: no\n\n' "$name"
        done < <(burrow_reverse_zone_names "$cidr" 2>/dev/null)
    done
}
