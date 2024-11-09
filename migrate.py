from tree_sitter import Language, Parser
from pathlib import Path
import mmap, collections, os, re, shutil, random, string
import nix

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

nixpkgs_path = Path("./nixpkgs").resolve()
all_packages_path = nixpkgs_path / "./pkgs/top-level/all-packages.nix"
temp_path = Path("/tmp/by-name-migrate").resolve()

# reusing my regex in https://github.com/NixOS/nixpkgs-vet/issues/107
by_name_restrict = re.compile("^((_[0-9])|[a-zA-Z])[a-zA-Z0-9_-]*$")


# query.captures doesn't seem to work for some reasons, so I write this dumb helper
def find_nodes(node, filter):
    nodes = []
    if filter(node):
        nodes.append(node)
    elif hasattr(node, "children"):
        for child in node.children:
            nodes = nodes + find_nodes(child, filter)
    return nodes


def find_path_nodes(node):
    filter_path = lambda node: (
        True
        if (
            node.type in {"path_expression", "hpath_expression", "spath_expression"}
            # no path interpolation (`./${a}`). spath has 0 child
            and len(node.children) < 2
        )
        else False
    )
    nodes = find_nodes(node, filter_path)
    return [str(n.text, encoding="utf8") for n in nodes]


def setup_ref():
    for nix_file_path in nixpkgs_path.rglob("*.nix"):
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
    return Path(f"./pkgs/by-name/{name[:2].lower()}/{name}")


# passthru.updateScript = writeShellScript ... " ... default.nix"
def has_update_script_path(node):
    filter_update_script = lambda node: (
        True
        if (
            node.type == "binding"
            and str(node.children[0].text, encoding="utf8").endswith("updateScript")
            and "default.nix" in str(node.children[2].text, encoding="utf8")
        )
        else False
    )
    nodes = find_nodes(node, filter_update_script)
    return nodes != []


def can_migrate(name, path):
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
        for subpath in path.rglob("*"):
            # Not referenced outside of path
            if subpath in nix_ref_rev:
                for rev in nix_ref_rev[subpath]:
                    if path not in rev.parents:
                        return False
            if not subpath.is_file():
                continue
            if subpath.suffix == ".nix":
                # Not containing paths that nixpkgs-vet doesn't like
                # like /foo/bar or <nixpkgs/...> or something inexistent
                if subpath in with_invalid_path:
                    return False
                if subpath in nix_ref:
                    for ref in nix_ref[subpath]:
                    # No reference to files outside of path
                    # we also don't want any file to reference back to default.nix
                    # including itself, since it will be changed to package.nix later
                        if path not in ref.parents or ref == path / "default.nix":
                            return False
                node = parser.parse(subpath.read_bytes()).root_node
                # No custom update script, thanks
                if has_update_script_path(node):
                    return False
            else:
                # may be referencing default.nix in another form, skip anyway
                if "default.nix" in subpath.read_text():
                    return False
        return True
    # TODO: support migrating files
    else:
        return False


def try_eval_by_name(packages_list):
    new_packages_list = []
    for pkg in packages_list:
        name, path, line = pkg
        # TODO: support migrating files
        # separate files copied there
        dest = (
            temp_path
            / "".join(random.choices(string.ascii_lowercase, k=10))
            / get_by_name(name)
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, dest)
        (dest / "default.nix").replace(dest / "package.nix")
        new_packages_list.append((name, dest / "package.nix"))

    eval_base = f"""
        with import {nixpkgs_path} {{
            config = {{ allowUnfree = true;
                allowBroken = true;
                allowUnsupportedSystem = true;
                allowAliases = false;
                allowInsecurePredicate = x: true;
            }};
        }};
    """.replace(
        "\n", " "
    )

    # send in bulk, because pythonix cannot reserve `import <nixpkgs>` value
    eval_same = []
    chunk = 1000
    for i in range(0, len(new_packages_list), chunk):
        eval_string = "["
        for pkg in new_packages_list[i : i + chunk]:
            name, path = pkg
            eval_string += f"""
                (let
                    new = (__tryEval (callPackage {path} {{}})).value;
                    old = (__tryEval {name}).value;
                in new ? outPath && old ? outPath && (let
                    new_out = __tryEval new.outPath;
                    old_out = __tryEval old.outPath;
                in new_out.success && old_out.success && new_out.value == old_out.value))
            """.replace(
                "\n", " "
            )
        eval_string += "]"
        try:
            eval_same += nix.eval(f"{eval_base} {eval_string}")
        except nix.NixError as e:
            print("eval error")
            print(e)
            continue  # We can't deal with this easily

    shutil.rmtree(temp_path)

    result = []
    for pkg, same in zip(packages_list, eval_same):
        if same:
            result.append(pkg)
    return result


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
    move_packages = []
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

        if can_migrate(name, path):
            move_packages.append((name, path, binding.start_point[0]))

    move_packages = try_eval_by_name(move_packages)

    for pkg in move_packages:
        name, path, line = pkg
        # TODO: support migrating files
        dest = nixpkgs_path / get_by_name(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        path.replace(dest)
        (dest / "default.nix").replace(dest / "package.nix")

    remove_lines = [pkg[2] for pkg in move_packages]

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
print("Now starting to migrate, this may still take a while, sadly")
migrate()
print("Done!")
