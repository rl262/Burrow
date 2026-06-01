#!/usr/bin/env bash
# Burrow — network helpers (sourceable so they can be unit-tested with bats).
#
#   source lib/burrow-net.sh
#   mask_cidr 192.168.1.44/24   # -> 192.168.1.0/24

# mask_cidr — echo the network-base form of an IPv4 CIDR (zero the host bits) so
# autodetected/typed addresses like 192.168.1.44/24 store as 192.168.1.0/24.
# Non-CIDR input is echoed back unchanged.
mask_cidr() {
  local cidr="$1" ip pfx o1 o2 o3 o4 hostbits addr net
  [[ "$cidr" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || { printf '%s' "$cidr"; return; }
  ip="${cidr%/*}"; pfx="${cidr#*/}"
  IFS=. read -r o1 o2 o3 o4 <<<"$ip"
  hostbits=$((32 - pfx)); addr=$(( (o1<<24)|(o2<<16)|(o3<<8)|o4 ))
  if   (( hostbits >= 32 )); then net=0
  elif (( hostbits <= 0  )); then net=$addr
  else net=$(( (addr >> hostbits) << hostbits )); fi
  printf '%d.%d.%d.%d/%d\n' $(((net>>24)&255)) $(((net>>16)&255)) $(((net>>8)&255)) $((net&255)) "$pfx"
}
