# Steps to reproduce

Add a git worktree (or checkout) in ./nixpkgs:

```
git worktree add /path/to/by-name-migrate/nixpkgs
```

Run script:

```
nix develop
python migrate.py
```

Stage and commit all files:

```
cd nixpkgs
git stage .
git commit -m "test"
```

Run nixpkgs-review:

```
nixpkgs-review rev HEAD
```

If anything goes wrong, remove:

```
git reset --hard HEAD~1
```