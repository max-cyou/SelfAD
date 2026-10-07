#!/bin/sh
# Wrapper around the image's dockerd-run script for the bundled runner.
#
# cgroup v2 refuses to delegate controllers to a subtree while the cgroup still
# holds processes of its own (EBUSY). The container's root cgroup is never
# empty here: s6-svscan and its children keep reappearing in it. So the runner
# cannot hand memory/cpu to the service containers it starts, and every service
# dies with "cannot enter cgroupv2 ... it is in threaded mode".
#
# Park the container's processes in a leaf cgroup, then enable the controllers
# in one atomic write, retrying while stragglers land back in the root.

exec 2>&1
set -eu

original=/opt/selfad/dockerd-run.original
cgroup_root=/sys/fs/cgroup

delegated() {
    cat "${cgroup_root}/cgroup.subtree_control" 2>/dev/null || echo ""
}

park_processes() {
    leaf=$1
    for pid in $(cat "${cgroup_root}/cgroup.procs" 2>/dev/null || true); do
        echo "${pid}" >"${leaf}/cgroup.procs" 2>/dev/null || true
    done
}

if [ -f "${original}" ] && [ -f "${cgroup_root}/cgroup.controllers" ]; then
    leaf="${cgroup_root}/selfad-init"
    if mkdir -p "${leaf}" 2>/dev/null; then
        attempt=0
        while [ "${attempt}" -lt 40 ]; do
            attempt=$((attempt + 1))
            park_processes "${leaf}"
            if echo "+memory +io +cpu +pids +cpuset" \
                >"${cgroup_root}/cgroup.subtree_control" 2>/dev/null; then
                echo "cgroup v2: delegated $(delegated) after ${attempt} attempt(s)"
                break
            fi
            sleep 0.25
        done
        if [ "${attempt}" -ge 40 ]; then
            echo "cgroup v2: could not delegate controllers; runner limits will fail" >&2
        fi
        park_processes "${leaf}"
    fi
fi

exec "${original}" "$@"
