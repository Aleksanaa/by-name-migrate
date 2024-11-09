{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    parts.url = "github:hercules-ci/flake-parts";
    nixpkgs-vet = {
      url = "github:NixOS/nixpkgs-vet/0.1.4";
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
        { pkgs, system, ... }:
        {
          devShells = {
            default = pkgs.mkShell {
              packages = [
                (pkgs.python3.withPackages (ps: [
                  ps.tree-sitter_0_21
                  ps.pythonix
                ]))
                pkgs.black
                pkgs.nixpkgs-review
                (
                  (import inputs.nixpkgs-vet {
                    inherit system;
                    inherit (inputs) nixpkgs;
                  }).build.overrideAttrs
                  # cannot pass check?
                  { doCheck = false; }
                )
              ];
              shellHook = ''
                export NIX_TREE_SITTER=${pkgs.tree-sitter-grammars.tree-sitter-nix}/parser
              '';
            };
          };
        };
    };
}
