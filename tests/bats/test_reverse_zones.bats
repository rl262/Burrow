#!/usr/bin/env bats
# Unit tests for the reverse-zone derivation (lib/burrow-reverse-zones.sh).
# A wrong derivation can collapse a /23 into an over-broad zone that shadows PTR
# recursion, so the prefix-length branches are covered here.

setup() {
  source "${BATS_TEST_DIRNAME}/../../lib/burrow-reverse-zones.sh"
}

@test "/24 -> one reverse zone" {
  run burrow_reverse_zone_names 192.168.1.0/24
  [ "$status" -eq 0 ]
  [ "$output" = "1.168.192.in-addr.arpa" ]
}

@test "/23 -> two covering /24 zones (never one over-broad zone)" {
  run burrow_reverse_zone_names 192.168.0.0/23
  [ "${#lines[@]}" -eq 2 ]
  [ "${lines[0]}" = "0.168.192.in-addr.arpa" ]
  [ "${lines[1]}" = "1.168.192.in-addr.arpa" ]
}

@test "/22 -> four covering /24 zones" {
  run burrow_reverse_zone_names 192.168.4.0/22
  [ "${#lines[@]}" -eq 4 ]
  [ "${lines[0]}" = "4.168.192.in-addr.arpa" ]
  [ "${lines[3]}" = "7.168.192.in-addr.arpa" ]
}

@test "wider than /20 -> no zones, returns non-zero" {
  run burrow_reverse_zone_names 10.0.0.0/16
  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "burrow_reverse_zones emits an Unbound stub-zone block" {
  run burrow_reverse_zones 192.168.1.0/24
  [[ "$output" == *'name: "1.168.192.in-addr.arpa"'* ]]
  [[ "$output" == *"stub-addr: 127.0.0.1@5300"* ]]
}
