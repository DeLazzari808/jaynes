# Packages

A package is a set of roles, the way an npm library is a set of functions. Jaynes itself has no roles: it runs the roles of the packages each project uses. It comes with two, `software` and `design`, and you can add your own, or your team's, for one project or for all.

## What you have

On the page, open **Roles**. It shows each installed package with its version, where it came from and its roles, and which projects use which. From a terminal, `jaynes packages` shows the same, and `jaynes roles`, run in a project, lists the roles a run there can start.

| Package | Roles | Where it comes from |
|---|---|---|
| software | build-feature, fix-bug, small-change | Comes with Jaynes, and follows its version |
| design | visual-identity, redesign, screen-design | Comes with Jaynes, and follows its version; examples |

What each one does is in [Roles that come with Jaynes](roles.md).

## Add a package

Use whichever is at hand:

- **On the page**: Roles, then Add a package. Give a folder or a git address, and the project, or leave the project empty for every project.
- **Ask your agent**: "use the roles in ~/my-team in this project". The plugin gives it the tools to do it, and it adds a package only when you ask.
- **From a terminal**, in the project:

```sh
jaynes add ~/my-team --project .                  # a folder of yours, for this project
jaynes add https://github.com/you/roles           # from git, for every project
jaynes add https://github.com/you/roles --ref v2  # a branch, tag or commit
jaynes add https://github.com/you/mono --folder roles  # a package in a folder of the repository
```

A package can come from three places:

- **A folder of yours** is linked, not copied: an edit applies to the next run, as `npm link` does.
- **A git repository** is installed at its newest commit, or at the branch, tag or commit you give. `jaynes update` moves it to the newest commit, unless you pinned a commit.
- **software or design**, by name, to bring one back after you removed it.

## One project or every project

Each project can have its own list of packages. A project with no list of its own uses the default list, which starts as `software` and `design`.

- Adding a package for one project starts that project's list from the default one and puts the new package first.
- Adding a package without a project puts it first in the default list.

The lists live in your Jaynes, in `~/.jaynes/packages.json`, never in the project's repository, so a project you work on for someone else gets no file from it.

## Which role a run takes

A run asks for a role by its name. Jaynes looks for that name in the project's packages, in order, and takes it from the first package that has it. So a package of yours with its own `screen-design` takes the place of the one in `design`, in the projects that list yours first, and nowhere else.

A run keeps the version of the role it started with, to the end, even if you update or remove the package meanwhile. Every installed version keeps its own folder for that.

## Remove a package

On the page, Roles, press Remove next to the package in the list you want it out of. Or:

```sh
jaynes remove my-team --project .   # out of this project's list
jaynes remove design                # out of the default list
```

Its installed versions stay, for the runs that use them.

## Make a package

A package is a folder with a `jaynes-package.toml` and its roles:

```text
my-team/
  jaynes-package.toml
  review_copy.py
  release_notes.toml
  checks.py
```

```toml
name = "my-team"          # lowercase letters, digits and dashes
version = "0.1.0"
about = "How my team reviews copy and writes release notes."
api = 1                   # the role API it is written for
```

Every role file (`.toml`) and every Python file with a role at its top level is one of the package's roles. Files whose names start with `_` are left out, and so is a file that does not load, so a half-written role never gets in the way. The Python files import each other relatively, `from . import checks`, so two versions of a package never mix.

[Write a role](write-a-role.md) shows how to write one. Check it before any agent runs it:

```sh
jaynes check review-copy --project .
```

To share the package, push the folder to a git repository; others add it with `jaynes add` and its address. When the role API changes in a way that breaks roles, Jaynes's `api` number goes up, and Jaynes refuses a package written for another one, saying so.
