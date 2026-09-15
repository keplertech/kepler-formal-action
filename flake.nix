{
  description = "Reproducible OCI image for the Kepler Formal GitHub Action";

  nixConfig = {
    extra-substituters = [ "https://keplertech.cachix.org" ];
    extra-trusted-public-keys = [
      "keplertech.cachix.org-1:f4jEdCmmFAS/Sd5OOKb1ddU61eJtRQytboKlBuSmZI8="
    ];
  };

  inputs = {
    kepler-formal.url = "git+https://github.com/keplertech/kepler-formal?ref=main&submodules=1";
    nixpkgs.follows = "kepler-formal/nixpkgs";
  };

  outputs =
    { self, nixpkgs, kepler-formal, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };
      keplerFormal = kepler-formal.packages.${system}.default;
      upstreamRevision = kepler-formal.rev or "unknown";
      actionRevision = self.rev or self.dirtyRev or "unknown";

      actionEntrypoint = pkgs.writeShellScriptBin "kepler-formal-action-entrypoint" ''
        exec ${pkgs.bash}/bin/bash ${./entrypoint.sh} "$@"
      '';

      imageRoot = pkgs.buildEnv {
        name = "kepler-formal-action-root";
        paths = [
          actionEntrypoint
          keplerFormal
          pkgs.bash
          pkgs.coreutils
          pkgs.gnugrep
          pkgs.dockerTools.fakeNss
        ];
        pathsToLink = [
          "/bin"
          "/etc"
        ];
      };

      container = pkgs.dockerTools.buildLayeredImage {
        name = "kepler-formal-action";
        tag = "nix";
        contents = [ imageRoot ];

        extraCommands = ''
          mkdir -p github/workspace tmp
          chmod 1777 tmp
        '';

        config = {
          Entrypoint = [ "/bin/kepler-formal-action-entrypoint" ];
          WorkingDir = "/github/workspace";
          Env = [
            "HOME=/tmp"
            "PATH=/bin"
            "TMPDIR=/tmp"
            "KEPLER_FORMAL_BIN=/bin/kepler-formal"
            "KEPLER_FORMAL_REF=${upstreamRevision}"
          ];
          Labels = {
            "org.opencontainers.image.description" = "Kepler Formal GitHub Action runtime";
            "org.opencontainers.image.licenses" = "Apache-2.0";
            "org.opencontainers.image.revision" = actionRevision;
            "org.opencontainers.image.source" = "https://github.com/keplertech/kepler-formal-action";
            "io.keplertech.kepler-formal.revision" = upstreamRevision;
          };
        };
      };
    in
    {
      packages.${system} = {
        inherit container;
        default = container;
      };

      checks.${system}.container = container;
    };
}
