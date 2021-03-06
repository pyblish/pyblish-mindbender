import os
import sys
import json
import types
import shutil
import logging
import inspect
import tempfile
import contextlib

from pyblish import api

from . import lib, schema
from . import (
    _registered_families,
    _registered_data,
    _registered_formats,
    _registered_loaders_paths,
    _registered_host,
    _registered_root,
)

from .vendor import six

self = sys.modules[__name__]

self.log = logging.getLogger("pyblish-mindbender")
self._is_installed = False


def install(host):
    """Install `host` into the running Python session.

    Arguments:
        host (module): A Python module containing the Pyblish
            mindbender host-interface.

    """

    # Optional host install function
    if hasattr(host, "install"):
        host.install()

    register_host(host)
    register_plugins()
    register_root(os.getenv("PROJECTDIR"))

    self._is_installed = True
    self.log.info("Successfully installed Pyblish Mindbender!")


def uninstall():
    try:
        registered_host().uninstall()
    except AttributeError:
        pass

    deregister_host()
    deregister_plugins()

    self.log.info("Successfully uninstalled Pyblish Mindbender!")


def is_installed():
    """Return state of installation

    Returns:
        True if installed, False otherwise

    """

    return self._is_installed


class Loader(object):
    families = list()

    def process(self, asset, subset, version, representation):
        pass


def discover_loaders():
    """Find and return available loaders

    """

    loaders = dict()

    # Include plug-ins from registered paths
    for path in _registered_loaders_paths:
        path = os.path.normpath(path)

        assert os.path.isdir(path), "%s is not a directory" % path

        for fname in os.listdir(path):
            abspath = os.path.join(path, fname)

            if not os.path.isfile(abspath):
                continue

            mod_name, mod_ext = os.path.splitext(fname)

            if not mod_ext == ".py":
                continue

            module = types.ModuleType(mod_name)
            module.__file__ = abspath

            try:
                with open(abspath) as f:
                    six.exec_(f.read(), module.__dict__)

                # Store reference to original module, to avoid
                # garbage collection from collecting it's global
                # imports, such as `import os`.
                sys.modules[mod_name] = module

            except Exception as err:
                print("Skipped: \"%s\" (%s)", mod_name, err)
                continue

            for plugin in loaders_from_module(module):
                if plugin.__name__ in loaders:
                    print("Duplicate plug-in found: %s", plugin)
                    continue

                loaders[plugin.__name__] = plugin

    return list(loaders.values())


def loaders_from_module(module):
    """Return plug-ins from module

    Arguments:
        module (types.ModuleType): Imported module from which to
            parse valid Pyblish plug-ins.

    Returns:
        List of plug-ins, or empty list if none is found.

    """

    loaders = list()

    for name in dir(module):

        # It could be anything at this point
        obj = getattr(module, name)

        if not inspect.isclass(obj):
            continue

        if not issubclass(obj, Loader):
            continue

        loaders.append(obj)

    return loaders


def register_loaders_path(path):
    path = os.path.normpath(path)
    _registered_loaders_paths.add(path)


def registered_loaders_paths():
    return list(_registered_loaders_paths)


def deregister_loaders_path(path):
    _registered_loaders_paths.pop(path)


def ls(root=None):
    """List available assets

    Return a list of available assets.

    The interface of this function, along with its schema, is designed
    to facilitate a potential transition into database-driven queries.

    Arguments:
        root (str, optional): Absolute path to asset directory

    A note on performance:
        This function is a generator, it scans the system one asset
        at a time. However, scanning implies both listing directories
        and opening files - one per asset per version.

        Therefore, performance drops combinatorially for each new
        version added to the project.

        In small pipelines - e.g. 100s of assets, with 10s of versions -
        this should not pose a problem.

        In large pipelines - e.g. 1000s of assets, with 100s of versions -
        this would likely become unbearable and manifest itself in
        surrounding areas of the pipeline where disk-access is
        critical; such as saving or loading files.

    ..note: The order of the list is undefined, but is typically alphabetical
        due to dependence on os.listdir()

    ..note: The order of versions returned is guaranteed to be sorted, so
        as to simplify retrieving the latest one via `versions[-1]`

    """

    assetsdir = root or registered_root()
    assert assetsdir is not None, ("No registered root.")

    for asset in lib.listdir(assetsdir):
        assetdir = os.path.join(assetsdir, asset)
        publishdir = lib.format_shared_dir(assetdir)

        asset_entry = {
            "schema": "pyblish-mindbender:asset-1.0",
            "name": asset,
            "subsets": list()
        }

        for subset in lib.listdir(publishdir):
            subsetdir = os.path.join(publishdir, subset)

            subset_entry = {
                "schema": "pyblish-mindbender:subset-1.0",
                "name": subset,
                "versions": list(),
            }

            asset_entry["subsets"].append(subset_entry)

            for version in lib.listdir(subsetdir):
                versiondir = os.path.join(subsetdir, version)
                fname = os.path.join(versiondir, ".metadata.json")

                try:
                    with open(fname) as f:
                        version_entry = json.load(f)

                except IOError:
                    self.log.warning("\"%s\" not found." % fname)
                    continue

                if version_entry.get("schema") != ("pyblish-mindbender"
                                                   ":version-1.0"):
                    self.log.warning("\"%s\" unsupported schema." % fname)
                    continue

                subset_entry["versions"].append(version_entry)

            # Sort versions by integer
            subset_entry["versions"].sort(key=lambda v: v["version"])

        schema.validate(asset_entry, "asset")

        yield asset_entry


def any_representation(version):
    """Pick any compatible representation.

    Arguments:
        version ("pyblish-mindbender:version-1.0"): Version from which
            to pick a representation, based on currently registered formats.

    """

    supported_formats = registered_formats()

    try:
        representation = next(
            rep for rep in version["representations"]
            if rep["format"] in supported_formats and
            rep["path"] != "{dirname}/source{format}"
        )

    except StopIteration:
        formats = list(r["format"] for r in version["representations"])
        raise ValueError(
            "No supported representations.\n\n"
            "Available representations:\n%s\n\n"
            "Supported representations:\n%s"
            % ("\n- ".join(formats),
               "\n- ".join(supported_formats))
        )

    return representation


@contextlib.contextmanager
def fixture(assets=["Asset1"], subsets=["animRig"], versions=1):
    """Build transient fixture of `assets` and `versions`

    Generate a temporary fixture of customisable assets
    with current metadata schema. This function is intended
    for use in tests and tutorials.

    Arguments:
        assets (list, optional): Names of assets to create,
            defaults to one asset named "Asset1"
        subsets (int, optional): Number of subsets for each asset,
            defaults to 1 subset.
        versions (list, optional): Number of versions of each subset,
            defaults to 1 version.

    Thread Safety:
        This function modifies globals state and is
        therefore not thread-safe.

    Usage:
        >>> with fixture(assets=["MyAsset1"]):
        ...    for asset in ls():
        ...       assert asset["name"] == "MyAsset1"
        ...

    """

    tempdir = tempfile.mkdtemp()

    for asset in assets:
        assetdir = os.path.join(tempdir, asset)
        shareddir = lib.format_shared_dir(assetdir)
        os.makedirs(shareddir)

        for subset in subsets:
            subsetdir = os.path.join(shareddir, subset)
            os.makedirs(subsetdir)

            for version in range(versions):
                version = lib.format_version(version + 1)
                versiondir = os.path.join(subsetdir, version)
                os.makedirs(versiondir)

                fname = os.path.join(versiondir, asset + ".ma")
                open(fname, "w").close()  # touch

                fname = os.path.join(versiondir, ".metadata.json")

                with open(fname, "w") as f:
                    json.dump({
                        "schema": "pyblish-mindbender:version-1.0",
                        "version": lib.parse_version(version),
                        "path": versiondir,
                        "time": "",
                        "author": "mottosso",
                        "source": os.path.join(
                            "{project}",
                            "maya",
                            "scenes",
                            "scene.ma"
                        ),
                        "representations": [
                            {
                                "schema": ("pyblish-mindbender:"
                                           "representation-1.0"),
                                "format": ".ma",
                                "path": os.path.join(
                                    "{dirname}",
                                    "%s{format}" % asset
                                ),
                            },
                        ]
                    }, f)

    # Keep track of original root
    _ = _registered_root["_"]

    try:
        _registered_root["_"] = tempdir
        yield tempdir
    finally:
        _registered_root["_"] = _
        shutil.rmtree(tempdir)


def register_root(path):
    """Register currently active root"""
    self.log.info("Registering root: %s" % path)
    _registered_root["_"] = path


def registered_root():
    """Return currently registered root"""
    return _registered_root["_"]


def register_format(format):
    """Register a supported format

    A supported format is used to determine which of any available
    representations are relevant to the currently registered host.

    """

    _registered_formats.append(format)


def register_host(host):
    missing = list()
    for member in ("load",
                   "create",
                   "ls",):
        if not hasattr(host, member):
            missing.append(member)

    assert not missing, (
        "Incomplete interface for host: '%s'\n"
        "Missing: %s" % (host, ", ".join(missing))
    )

    _registered_host["_"] = host


def register_plugins():
    """Register accompanying plugins"""
    module_path = sys.modules[__name__].__file__
    package_path = os.path.dirname(module_path)
    plugins_path = os.path.join(package_path, "plugins")
    api.register_plugin_path(plugins_path)


def register_data(key, value, help=None):
    """Register new default attribute

    Arguments:
        key (str): Name of data
        value (object): Arbitrary value of data
        help (str, optional): Briefly describe

    """

    _registered_data[key] = value


def deregister_data(key):
    _registered_data.pop(key)


def register_family(name,
                    label=None,
                    data=None,
                    help=None,
                    loader=None):
    """Register family and attributes for family

    Arguments:
        name (str): Name of family, e.g. mindbender.model
        label (str): Nice name for family, e.g. Model
        data (dict, optional): Additional data, see
            :func:`register_data` for docstring on members
        help (str, optional): Briefly describe this family

    """

    _registered_families[name] = {
        "name": name,
        "label": label,
        "data": data or {},
        "help": help or "",
        "loader": loader
    }


def deregister_family(name):
    _registered_families.pop(name)


def registered_formats():
    return _registered_formats[:]


def registered_families():
    return _registered_families.copy()


def registered_data():
    return _registered_data.copy()


def registered_host():
    return _registered_host["_"]


def deregister_plugins():
    from . import plugins
    plugin_path = os.path.dirname(plugins.__file__)

    try:
        api.deregister_plugin_path(plugin_path)
    except ValueError:
        self.log.warning("pyblish-mindbender plug-ins not registered.")


def deregister_host():
    _registered_host["_"] = default_host()


def default_host():
    """A default host, in place of anything better

    This may be considered as reference for the
    interface a host must implement. It also ensures
    that the system runs, even when nothing is there
    to support it.

    """

    host = types.ModuleType("defaultHost")
    host.__dict__.update({
        "ls": lambda: [],
        "load": lambda subset, version=-1, representation=None: None,
        "create": lambda name, family, nodes=None: "my_instance",
    })

    return host


def debug_host():
    """A debug host, useful to debugging features that depend on a host"""
    host = types.ModuleType("debugHost")
    host.__dict__.update({
        "ls": lambda: [],
        "load": lambda subset, version=-1, representation=None:
            sys.stdout.write(json.dumps({
                "subset": subset,
                "version": version,
                "representation": representation
            }, indent=4) + "\n"),
        "create": lambda name, family, nodes=None:
            sys.stdout.write(json.dumps({
                "name": name,
                "family": family,
            }, indent=4))
    })

    return host
