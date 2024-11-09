{
  buildPythonPackage,
  python-nix-src,
  cffi,
  nixVersions,
  pkgconfig,
}:

buildPythonPackage {
  name = "python-nix";

  src = python-nix-src;

  propagatedBuildInputs = [ cffi ];

  # only compatible version?
  buildInputs = [ nixVersions.nix_2_22 ];

  nativeBuildInputs = [
    pkgconfig
  ];

  pythonImportsCheck = [
    "nix"
    "nix.util"
    "nix.store"
    "nix.expr"
  ];
}
