# ---------------------------------------------------------------------------
# shell.nix — Nix development shell for Sdlicit
#
# Usage:
#   nix-shell            # enter the dev shell (auto-syncs deps on first run)
#   nix-shell --pure     # stricter: only Nix-provided binaries on PATH
#
# What this shell provides
#   • python3.12  — pinned CPython interpreter
#   • uv          — fast Python package / venv manager
#   • nodejs_22   — required for the VS Code extension in extension/
#
# On every entry the shellHook will:
#   1. sync deps and activate .venv/
#   2. patch compiled wheel rpaths (NixOS-specific)
# ---------------------------------------------------------------------------
{
  pkgs ? import <nixpkgs> { },
}:

pkgs.mkShell {
  name = "sdlicit-dev";

  packages = with pkgs; [
    # --- Python runtime --------------------------------------------------
    python312 # CPython 3.12 — matches requires-python in pyproject.toml

    # --- Package / venv management ---------------------------------------
    uv # replaces pip + virtualenv; reads pyproject.toml natively

    # --- VS Code extension toolchain -------------------------------------
    nodejs_22 # TypeScript compilation for extension/

    # --- Handy extras (optional, remove if you want a leaner shell) ------
    git
    gnumake
    patchelf # needed to fix libstdc++ rpath in pre-built wheels
  ];

  # Make UV use the Nix-provided Python instead of downloading its own.
  # This keeps the interpreter path stable across machines.
  UV_PYTHON = "${pkgs.python312}/bin/python3";

  # Prevent UV from downloading a standalone Python toolchain.
  UV_PYTHON_DOWNLOADS = "never";

  # Expose core libraries so that compiled Python wheels (Pillow, pyzmq, numpy, etc.)
  # can map their internal dependencies to host binaries cleanly.
  LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
    pkgs.stdenv.cc.cc.lib # libstdc++.so.6 / libgcc_s.so.1
    pkgs.glibc # libc.so.6 / core POSIX bindings
    pkgs.zlib # libz.so.1
    pkgs.zstd # libzstd.so.1
  ];

  shellHook = ''
            # 1. Sync Python dependencies into .venv/ (uncomment to sync automatically)
            # uv sync --group dev

            # 2. Activate the virtualenv
            [ -d .venv ] && source .venv/bin/activate

            # 3. Fix NixOS-specific wheel linking issues.
            #
            #    Manylinux wheels bundle native deps in <pkg>.libs/ next to the
            #    package. Two independent problems show up on NixOS:
            #      a) sibling resolution — a bundled lib that itself pulls in
            #         another bundled lib (e.g. libtiff -> libzstd) fails to
            #         resolve because RPATH does not propagate transitively.
            #      b) libstdc++ resolution — compiled extensions link against
            #         a libstdc++ that only exists via Nix, not at the FHS
            #         paths the wheel expects.
            #
            #    A handful of wheels (notably Pillow's bundled libwebp) ship
            #    a .so that crashes `patchelf` outright. That crash still
            #    terminates by SIGSEGV, and bash's own job-control reporting
            #    for a terminated foreground process writes straight to the
            #    controlling terminal — bypassing this command's own stdio
            #    redirects entirely, which is why past versions of this hook
            #    printed a scary "Segmentation fault (core dumped)" line even
            #    though the loop already treated the failure as non-fatal.
            #    Silencing it requires redirecting the *shell's own* stderr
            #    for the duration (see the `exec 9>&2 … exec 2>&9 9>&-`
            #    bracket below), not just the child command's.
            _is_elf() {
              # ELF magic = 0x7F 'E' 'L' 'F'. Guards every patchelf call below —
              # skips malformed/non-ELF files that would otherwise crash patchelf.
              [ -f "$1" ] && [ ! -L "$1" ] || return 1
              [ "$(head -c 4 "$1" 2>/dev/null | od -An -c | tr -d ' \n')" = '177ELF' ]
            }
            _safe_patch() {
              local so="$1"; shift
              _is_elf "$so" || return 0
              ( patchelf "$@" "$so" ) >/dev/null 2>&1 || true
            }
            _safe_patch_rpath() {
              _is_elf "$1" || return 0
              ( patchelf --print-rpath "$1" ) 2>/dev/null || true
            }

            if [ -d .venv ]; then
              # Disable core dump files for this shell, and silence bash's own
              # SIGSEGV job-control notice for the duration of the patch loops
              # (see comment above) — both restored/scoped to this block only.
              ulimit -c 0
              exec 9>&2
              exec 2>/dev/null

              find .venv -type d -name '*.libs' 2>/dev/null | while read -r libsdir; do
                for so in "$libsdir"/*.so*; do
                  [ -f "$so" ] || continue
                  [ -L "$so" ] && continue
                  cur=$( _safe_patch_rpath "$so" )
                  case "$cur" in
                    *'$ORIGIN'*) ;;
                    "")  _safe_patch "$so" --set-rpath '$ORIGIN' ;;
                    *)   _safe_patch "$so" --set-rpath '$ORIGIN':"$cur" ;;
                  esac
                done
              done

              find .venv \( -name '*.so' -o -name '*.so.*' \) 2>/dev/null | while read -r so; do
                [ -L "$so" ] && continue
                ldd "$so" 2>/dev/null | grep -q 'not found' || continue
                cur=$( _safe_patch_rpath "$so" )
                case ":$cur:" in
                  *:${pkgs.stdenv.cc.cc.lib}/lib:*) ;;
                  *) _safe_patch "$so" --add-rpath "${pkgs.stdenv.cc.cc.lib}/lib" ;;
                esac
              done

              # c) Console-script binaries in .venv/bin/ (ruff, mypy's compiled
              #    parts, etc). uv installs some tools as native ELF
              #    executables, not Python wrapper scripts, built against a
              #    generic /lib64/ld-linux-x86-64.so.2 loader that does not
              #    exist on NixOS. Point them at the Nix-provided loader.
              find .venv/bin -maxdepth 1 -type f 2>/dev/null | while read -r bin; do
                _is_elf "$bin" || continue
                interp=$( ( patchelf --print-interpreter "$bin" ) 2>/dev/null || echo "" )
                case "$interp" in
                  /nix/store/*) ;;
                  *) _safe_patch "$bin" --set-interpreter "${pkgs.stdenv.cc.bintools.dynamicLinker}" \
                       --add-rpath "${pkgs.stdenv.cc.cc.lib}/lib:${pkgs.glibc}/lib" ;;
                esac
              done

              exec 2>&9 9>&-
            fi

            echo "[nix-shell] sdlicit ready — Python $(python --version 2>&1 | cut -d' ' -f2)"
  '';
}
