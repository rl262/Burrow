#!/usr/bin/env bats
# Unit tests for mask_cidr (lib/burrow-net.sh).

setup() {
  source "${BATS_TEST_DIRNAME}/../../lib/burrow-net.sh"
}

@test "mask_cidr zeros host bits on a /24" {
  run mask_cidr 192.168.1.44/24
  [ "$status" -eq 0 ]
  [ "$output" = "192.168.1.0/24" ]
}

@test "mask_cidr aligns a /23 to its block boundary" {
  run mask_cidr 192.168.3.7/23
  [ "$output" = "192.168.2.0/23" ]
}

@test "mask_cidr handles a /8" {
  run mask_cidr 10.9.8.7/8
  [ "$output" = "10.0.0.0/8" ]
}

@test "mask_cidr keeps the host on a /32" {
  run mask_cidr 192.168.1.5/32
  [ "$output" = "192.168.1.5/32" ]
}

@test "mask_cidr passes non-CIDR input through unchanged" {
  run mask_cidr "not-a-cidr"
  [ "$output" = "not-a-cidr" ]
}
