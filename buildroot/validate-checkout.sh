#!/bin/sh
# Validate that a Buildroot working tree is the exact, clean pinned source.
set -eu

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
	echo "usage: validate-checkout.sh BUILDROOT_DIR VERSION EXPECTED_COMMIT [--allow-unverified]" >&2
	exit 2
fi

buildroot_dir=$1
version=$2
expected_commit=$3
allow_unverified=${4:-}

if [ -n "$allow_unverified" ] && [ "$allow_unverified" != "--allow-unverified" ]; then
	echo "validate-checkout.sh: unknown option: $allow_unverified" >&2
	exit 2
fi

fail_or_warn() {
	if [ "$allow_unverified" = "--allow-unverified" ]; then
		echo "build.sh: WARNING: $* (--allow-unverified-buildroot)" >&2
		return 0
	fi
	echo "build.sh: ERROR: $*" >&2
	exit 1
}

command -v git >/dev/null 2>&1 || fail_or_warn "git is required to validate Buildroot"

if [ ! -d "$buildroot_dir/.git" ]; then
	fail_or_warn "existing Buildroot directory is not a Git checkout: $buildroot_dir"
	printf '%s\n' unknown
	exit 0
fi

actual_commit=$(git -C "$buildroot_dir" rev-parse --verify HEAD 2>/dev/null) || {
	fail_or_warn "cannot resolve Buildroot HEAD in $buildroot_dir"
	printf '%s\n' unknown
	exit 0
}

if ! printf '%s\n' "$expected_commit" | grep -Eq '^[0-9a-f]{40}$'; then
	fail_or_warn "configured Buildroot commit is not a full lowercase Git object ID: $expected_commit"
elif [ "$actual_commit" != "$expected_commit" ]; then
	fail_or_warn "Buildroot $version must be $expected_commit, but $buildroot_dir is $actual_commit"
fi

status=$(git -C "$buildroot_dir" status --porcelain --untracked-files=normal) || {
	fail_or_warn "cannot inspect Buildroot worktree state in $buildroot_dir"
	status=unknown
}
if [ -n "$status" ]; then
	fail_or_warn "Buildroot checkout is dirty: $buildroot_dir"
fi

printf '%s\n' "$actual_commit"