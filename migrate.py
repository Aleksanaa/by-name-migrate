from tree_sitter import Language, Parser
from pathlib import Path
import mmap, collections, os, re

NIX_LANGUAGE = Language(
    os.environ["NIX_TREE_SITTER"],
    "nix",
)

parser = Parser()
parser.set_language(NIX_LANGUAGE)

# These are packages that are failing (or may fail) but haven't known why
# There's a `res.foo`, what is `res`?
dislike_packages = {
    "jing-trang",
    "pcre",
    "espeak-ng",
    "faust2",
}

nix_ref = {}
nix_ref_rev = {}
with_invalid_path = []

all_packages_path = Path("./nixpkgs/pkgs/top-level/all-packages.nix").resolve()

# reusing my regex in https://github.com/NixOS/nixpkgs-vet/issues/107
by_name_restrict = re.compile("^((_[0-9])|[a-zA-Z])[a-zA-Z0-9_-]*$")


# query.captures doesn't seem to work for some reasons, so I write this dumb helper
def find_path_nodes(node):
    paths_string = []
    # we don't want to deal with path interpolation like `./${a}` for now
    if (
        node.type in {"path_expression", "hpath_expression", "spath_expression"}
        and len(node.children) < 2 # spath has zero children
    ):
        path_string = str(node.text, encoding="utf8")
        paths_string.append(path_string)
    elif hasattr(node, "children"):
        for child in node.children:
            paths_string = paths_string + find_path_nodes(child)
    return paths_string


def setup_ref():
    for nix_file_path in Path("./nixpkgs").resolve().rglob("*.nix"):
        # Skip directory and symlink
        if not nix_file_path.is_file():
            continue
        nix_tree = parser.parse(nix_file_path.read_bytes())
        for path_string in find_path_nodes(nix_tree.root_node):
            # we also don't want to deal with `<nixpkgs/pkgs/...>`
            if not (path_string.startswith("./") or path_string.startswith("../")):
                with_invalid_path.append(nix_file_path)
                continue
            path_obj = (nix_file_path / "../" / path_string).resolve()
            if not path_obj.exists():
                with_invalid_path.append(nix_file_path)
                continue
            if nix_file_path in nix_ref:
                nix_ref[nix_file_path].append(path_obj)
            else:
                nix_ref[nix_file_path] = [path_obj]
            if path_obj in nix_ref_rev:
                nix_ref_rev[path_obj].append(nix_file_path)
            else:
                nix_ref_rev[path_obj] = [nix_file_path]


def get_by_name(name):
    return Path(f"./nixpkgs/pkgs/by-name/{name[:2].lower()}/{name}")


def try_migrate(name, path):
    # move_target = [];
    if path.is_dir():
        if not (path / "default.nix").is_file():
            return False
        # function??
        if len(name) < 2 or by_name_restrict.match(name) == None:
            return False
        # is this possible (since we have all_packages.nix)?
        if path not in nix_ref_rev:
            return False
        # path itself referenced by other files outside of path
        # except all-packages.nix
        for rev in nix_ref_rev[path]:
            if rev != all_packages_path and path not in rev.parents:
                return False
        for file in path.rglob("*"):
            # Not containing paths that nixpkgs-vet doesn't like
            # like /foo/bar or <nixpkgs/...> or something inexistent
            if file in with_invalid_path:
                return False
            # Not referenced and no reference outside of path
            if file in nix_ref_rev:
                for rev in nix_ref_rev[file]:
                    if path not in rev.parents:
                        return False
            if file in nix_ref:
                for ref in nix_ref[file]:
                    # we also don't want any file to reference back to default.nix
                    # including itself, since it will be changed to package.nix later
                    if path not in ref.parents or ref == path / "default.nix":
                        return False
            # No custom update script, thanks
            if file.suffix not in {".patch", ".diff", ".nix"} and "update" in file.name:
                return False
        dest = get_by_name(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
        (dest / "default.nix").replace(dest / "package.nix")
        return True
    # TODO: support migrating files
    else:
        return False


def migrate():
    ap_lines = all_packages_path.open("r").readlines()
    ap_top = parser.parse(all_packages_path.read_bytes()).root_node
    ap_node = (
        ap_top.children[1]  # `{ lib, `... function, 0 is comment
        .children[2]  # `res:` function
        .children[2]  # `pkgs:` function
        .children[2]  # `super:` function
        .children[2]  # `with pkgs;`
        .children[3]  # main attrset
        .children[-2]  # bindings
    )
    assert ap_node.type == "binding_set"
    paths = [
        (all_packages_path / "../" / path).resolve()
        for path in find_path_nodes(ap_node)
    ]
    # collect duplicate paths in all-packages.nix
    dup_paths = [
        item for item, count in collections.Counter(paths).items() if count > 1
    ]
    remove_lines = []
    for binding in ap_node.children:
        # Also can be comment
        if binding.type != "binding":
            continue
        # Be conservative: only one line (in the same row)
        if binding.start_point[0] != binding.end_point[0]:
            continue
        if not all(
            byte in {" ", "\t"}
            for byte in ap_lines[binding.start_point[0]][: binding.start_point[1]]
        ):
            continue
        # TODO: We don't deal with things after the binding
        # obviously there should not be but?
        right_expr = binding.children[2]
        try:
            if (
                right_expr.type
                != "apply_expression"  # callPackage ../foo.nix { foo = bar; }
                or right_expr.children[0].type
                != "apply_expression"  # callPackage ../foo.nix
                or right_expr.children[1].type != "attrset_expression"  # { foo = bar; }
                or len(right_expr.children[1].children)
                != 2  # We only want to deal with {}, for now
                or right_expr.children[0].children[1].type
                != "path_expression"  # ../foo.nix
                or len(right_expr.children[0].children[1].children)
                != 1  # no path interpolation
                or str(right_expr.children[0].children[0].text, encoding="utf8")
                != "callPackage"
            ):
                continue
        except IndexError:
            continue
        # path to definition
        relpath = Path(str(right_expr.children[0].children[1].text, encoding="utf8"))
        path = (all_packages_path / "../" / relpath).resolve()
        # someone is calling a path twice, and we obviously don't like it
        if path in dup_paths:
            continue
        name = str(binding.children[0].text, encoding="utf8")
        if name in dislike_packages:
            continue

        success = try_migrate(name, path)
        if not success:
            continue
        remove_lines.append(binding.start_point[0])

    with all_packages_path.open("w") as ap:
        last_is_blank = False
        for num, line in enumerate(ap_lines):
            is_blank = len(line) == 1
            if num not in remove_lines and not (last_is_blank and is_blank):
                ap.write(line)
                last_is_blank = False
            else:
                last_is_blank = True


print("Setting up reference table, this can take a while")
setup_ref()
print("Now starting to migrate")
migrate()
print("Done!")
