{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    parts.url = "github:hercules-ci/flake-parts";
    python-nix-src = {
      url = "github:tweag/python-nix";
      flake = false;
    };
  };

  outputs =
    inputs:
    inputs.parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      perSystem =
        { pkgs, ... }:
        {
          devShells = {
            default = pkgs.mkShell {
              packages = [
                (pkgs.python3.withPackages (ps: [
                  ps.tree-sitter_0_21
                  (ps.callPackage ./python.nix { inherit (inputs) python-nix-src; })
                ]))
                pkgs.black
                pkgs.nixpkgs-review
              ];
              shellHook = ''
                export NIX_TREE_SITTER=${pkgs.tree-sitter-grammars.tree-sitter-nix}/parser
              '';
            };
          };
        };
    };
}
