# flake.nix — Nix flake wrapper around shell.nix.
#
# Provides the same dev shell as `nix-shell` (Python 3.12, uv, Node 22,
# the NixOS wheel-linking fixes) through the flake interface:
#
#   nix develop            # enter the dev shell
#   nix develop --command uv run sdlicit-server
#
# This does not (yet) provide a `nix build`/`nix run` package output for
# the application itself — Sdlicit's dependency set (dspy, lightrag-hku,
# and their transitive PyPI-only dependencies) is not readily expressible
# as a nixpkgs derivation without a uv-aware Nix builder (e.g. uv2nix).
# Until that lands, `nix develop` + `uv run` is the supported path on Nix.
{
  description = "Sdlicit — knowledge-grounded LLM framework for structured SDLC artifact creation";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = import ./shell.nix { inherit pkgs; };
      }
    );
}
