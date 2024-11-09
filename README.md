# Steps to reproduce

Add a git worktree (or checkout) in ./nixpkgs:

```
git worktree add /path/to/by-name-migrate/nixpkgs
```

The script will create `/tmp/by-name-migrate`, and copy some files (about 10000 nix files) inside to test evaluation. Edit `temp_path` in script if you don't want this.

Run script:

```
nix develop
python migrate.py
```

It takes about 4 minutes. No multi-threading optimization here.

Stage and commit all files:

```
cd nixpkgs
git stage .
git commit -m "test"
```

Review (in nixpkgs root):

```
nixpkgs-review rev HEAD
nixpkgs-vet --base . .
```

Also you can run ofborg eval checks, see [Running meta checks locally](https://github.com/NixOS/ofborg/tree/released?tab=readme-ov-file#running-meta-checks-locally).

If anything goes wrong, remove:

```
git reset --hard HEAD~1
```