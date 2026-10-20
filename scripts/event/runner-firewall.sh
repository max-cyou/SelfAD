#!/bin/sh
# Dedicated runner only. Never flush host chains or alter SSH access.
set -eu
control_ip=${1:?Usage: runner-firewall.sh CONTROL_PLANE_IPV4}
python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1])' "$control_ip"
command -v iptables >/dev/null
command -v ip6tables >/dev/null
rule() {
    binary=$1; shift
    if "$binary" -w 5 -C INPUT "$@" 2>/dev/null; then
        "$binary" -w 5 -D INPUT "$@"
    fi
    "$binary" -w 5 -I INPUT 1 "$@"
}
rule iptables -p tcp --dport 2376 -s "$control_ip" -m comment --comment selfad-api-allow -j ACCEPT
rule iptables -p tcp --dport 2376 ! -s "$control_ip" -m comment --comment selfad-api-deny -j DROP
rule iptables -p tcp --dport 2375 -m comment --comment selfad-no-plaintext -j DROP
rule iptables -i sad+ -m comment --comment selfad-job-no-host -j DROP
rule ip6tables -p tcp --dport 2376 -m comment --comment selfad-api-deny-v6 -j DROP
rule ip6tables -p tcp --dport 2375 -m comment --comment selfad-no-plaintext-v6 -j DROP
rule ip6tables -i sad+ -m comment --comment selfad-job-no-host-v6 -j DROP
