import support

import contextlib
import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import epubkit
from reader import cli, library

LAUNCHER = os.path.join(support.ROOT, "bin", "reader")
DRM_SENTENCE = "This book is protected by DRM and can't be opened."


class CliCase(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = scratch.name
        self.books = os.path.join(self.root, "Books")
        self.cache = os.path.join(self.root, "cache")
        os.makedirs(self.books)

    def run_main(self, *argv):
        """Exit status, the parsed JSON object, and what went to stderr."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = cli.main(list(argv))
        text = out.getvalue()
        self.assertEqual(text.count("\n"), 1, text)
        return status, json.loads(text), err.getvalue()

    def test_a_cache_left_open_by_an_earlier_version_is_closed(self):
        mask = os.umask(0o022)
        self.addCleanup(os.umask, mask)
        os.makedirs(os.path.join(self.cache, "books"))
        os.chmod(self.cache, 0o755)
        status, result, _ = self.run_main("--cache", self.cache, "--dir", self.books, "scan")
        self.assertEqual((status, result["ok"]), (0, True))
        self.assertEqual(os.stat(self.cache).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(os.path.join(self.cache, "index.json")).st_mode & 0o777, 0o600)

    def failure(self, *argv):
        status, result, _ = self.run_main(*argv)
        self.assertEqual(status, 1)
        self.assertEqual(sorted(result), ["error", "ok"])
        self.assertIs(result["ok"], False)
        self.assertEqual(sorted(result["error"]), ["code", "message"])
        self.assertNotIn("Traceback", result["error"]["message"])
        return result["error"]["code"], result["error"]["message"]

    def write(self, name, data):
        path = os.path.join(self.books, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path


class CommandsTest(CliCase):
    def test_scan(self):
        epubkit.make(self.books, title="Listed")
        status, result, _ = self.run_main("scan", "--cache", self.cache, "--dir", self.books)
        self.assertEqual(status, 0)
        self.assertEqual((result["ok"], result["dirs"]), (True, [self.books]))
        self.assertEqual([entry["title"] for entry in result["books"]], ["Listed"])
        self.assertTrue(os.path.isfile(os.path.join(self.cache, "index.json")))

    def test_scan_several_directories_and_option_forms(self):
        other = os.path.join(self.root, "More")
        os.makedirs(other)
        epubkit.make(self.books, "a.epub", title="A", docs=(("c.xhtml", "<p>a</p>"),))
        epubkit.make(other, "b.epub", title="B", docs=(("c.xhtml", "<p>b</p>"),))
        status, result, _ = self.run_main("--cache=" + self.cache, "scan", "--dir=" + self.books, "--dir", other)
        self.assertEqual(status, 0)
        self.assertEqual(result["dirs"], [self.books, other])
        self.assertEqual([entry["title"] for entry in result["books"]], ["A", "B"])

    def test_scan_expands_home(self):
        epubkit.make(self.books, title="Home")
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            status, result, _ = self.run_main("scan", "--cache", "~/cache", "--dir", "~/Books")
        self.assertEqual((status, result["dirs"]), (0, [self.books]))
        self.assertTrue(os.path.isfile(os.path.join(self.cache, "index.json")))

    def test_open(self):
        path = epubkit.make(self.books)
        status, result, _ = self.run_main("open", "--cache", self.cache, path)
        self.assertEqual(status, 0)
        self.assertEqual(sorted(result), ["book", "cached", "key", "ok"])
        self.assertEqual((result["ok"], result["cached"]), (True, False))
        self.assertEqual(result["book"], os.path.join(self.cache, "books", result["key"], "book.json"))
        with open(result["book"], encoding="utf-8") as handle:
            self.assertEqual(len(json.load(handle)["blocks"]), 4)
        status, again, _ = self.run_main("--cache", self.cache, "open", "--", path)
        self.assertEqual((status, again), (0, dict(result, cached=True)))

    def test_open_accepts_any_name_after_the_separator(self):
        path = epubkit.make(self.books, "--cache.epub")
        with contextlib.chdir(self.books):
            status, result, _ = self.run_main("--cache", self.cache, "open", "--", "--cache.epub")
        self.assertEqual(status, 0)
        with open(result["book"], encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["path"], path)

    def test_info(self):
        path = epubkit.make(self.books, title="Informed")
        status, result, _ = self.run_main("info", path)
        self.assertEqual(status, 0)
        self.assertEqual((result["ok"], result["title"], result["blocks"], len(result["toc"])), (True, "Informed", 4, 2))
        self.assertEqual(os.listdir(self.root), ["Books"])

    def test_default_cache(self):
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": os.path.join(self.root, "xdg")}):
            self.assertEqual(cli.default_cache(), os.path.join(self.root, "xdg", "omarchy-reader"))
            status, _, _ = self.run_main("scan", "--dir", self.books)
        self.assertEqual(status, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "xdg", "omarchy-reader", "index.json")))
        with mock.patch.dict(os.environ, {"HOME": self.root}):
            os.environ.pop("XDG_CACHE_HOME", None)
            self.assertEqual(cli.default_cache(), os.path.join(self.root, ".cache", "omarchy-reader"))


class ErrorsTest(CliCase):
    def test_missing(self):
        code, message = self.failure("open", "--cache", self.cache, os.path.join(self.books, "gone.epub"))
        self.assertEqual(code, "missing")
        self.assertTrue(message.endswith("."))
        self.assertEqual(self.failure("info", os.path.join(self.books, "gone.epub"))[0], "missing")

    def test_unsupported(self):
        code, message = self.failure("open", "--cache", self.cache, self.write("manual.pdf", b"%PDF-1.4"))
        self.assertEqual(code, "unsupported")
        self.assertIn("PDF", message)
        self.assertEqual(self.failure("open", "--cache", self.cache, self.write("notes.docx", b"words"))[0], "unsupported")

    def test_drm(self):
        noise = bytes(range(256)) * 8
        path = epubkit.make(self.books, docs=(("ch1.xhtml", noise),), ncx_points=False,
                            root_files={"META-INF/encryption.xml": epubkit.encryption((epubkit.AES, "OEBPS/ch1.xhtml"))})
        self.assertEqual(self.failure("open", "--cache", self.cache, path), ("drm", DRM_SENTENCE))

    def test_corrupt(self):
        code, message = self.failure("open", "--cache", self.cache, self.write("bad.epub", b"never an archive"))
        self.assertEqual(code, "corrupt")
        self.assertNotIn("zip", message.lower())

    def test_internal(self):
        with mock.patch.object(library, "scan", side_effect=RuntimeError("secret detail")):
            status, result, err = self.run_main("scan", "--cache", self.cache)
        self.assertEqual((status, result["error"]["code"]), (1, "internal"))
        self.assertNotIn("secret detail", result["error"]["message"])
        self.assertIn("secret detail", err)
        self.assertIn("Traceback", err)

    def test_running_out_of_memory_is_an_answer_too(self):
        with mock.patch.object(library, "open_book", side_effect=MemoryError):
            code, message = self.failure("open", "--cache", self.cache, "--", "any.epub")
        self.assertEqual(code, "corrupt")
        self.assertIn("memory", message)

    def test_an_empty_option_value_is_a_mistake_not_the_current_directory(self):
        here = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, here)
        for argv in (["--cache", "", "scan", "--dir", self.books], ["--cache=", "scan", "--dir", self.books],
                     ["--cache", self.cache, "scan", "--dir", ""], ["--cache", self.cache, "scan", "--dir="]):
            with self.subTest(argv=argv):
                code, _ = self.failure(*argv)
                self.assertEqual(code, "internal")
        self.assertEqual(os.listdir(self.root), ["Books"])

    def test_a_full_disk_is_said_plainly(self):
        full = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(library, "open_book", side_effect=full):
            code, message = self.failure("open", "--cache", self.cache, "--", "any.epub")
        self.assertEqual(code, "internal")
        self.assertIn("no room left", message)

    def test_usage_errors(self):
        for argv in ([], ["frobnicate"], ["open"], ["open", "a", "b"], ["scan", "extra"], ["scan", "--cache"],
                     ["scan", "--bogus"], ["info"], ["open", "--dir", "x", "y"], ["-h"], ["--help"]):
            with self.subTest(argv=argv):
                code, _ = self.failure(*argv)
                self.assertEqual(code, "internal")

    def test_stray_output_never_reaches_stdout(self):
        def chatty(dirs, cache):
            print("debugging leftovers")
            return {"ok": True, "dirs": [], "books": []}

        with mock.patch.object(library, "scan", chatty):
            status, result, err = self.run_main("scan", "--cache", self.cache)
        self.assertEqual((status, result), (0, {"ok": True, "dirs": [], "books": []}))
        self.assertIn("debugging leftovers", err)


class LauncherTest(CliCase):
    def launch(self, *argv, env=None):
        environment = dict(os.environ, **(env or {}))
        return subprocess.run([sys.executable, "-B", LAUNCHER, *argv], capture_output=True, env=environment, timeout=60)

    def test_one_json_object_on_stdout(self):
        epubkit.make(self.books, title="Café 书", creators=("Wells, H. G.",))
        done = self.launch("scan", "--cache", self.cache, "--dir", self.books, env={"LC_ALL": "C", "PYTHONIOENCODING": "ascii"})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count(b"\n"), 1)
        result = json.loads(done.stdout.decode("utf-8"))
        self.assertEqual((result["books"][0]["title"], result["books"][0]["author"]), ("Café 书", "H. G. Wells"))

        opened = self.launch("open", "--cache", self.cache, "--", result["books"][0]["path"])
        self.assertEqual(opened.returncode, 0, opened.stderr)
        self.assertEqual(json.loads(opened.stdout)["key"], result["books"][0]["key"])

    def test_failure_status_and_object(self):
        done = self.launch("open", "--cache", self.cache, os.path.join(self.books, "gone.epub"))
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads(done.stdout)["error"]["code"], "missing")
        done = self.launch("nonsense")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(json.loads(done.stdout)["error"]["code"], "internal")

    def test_the_process_has_a_ceiling_on_its_memory(self):
        probe = ("import resource, runpy, sys\n"
                 "from reader import cli\n"
                 "cli.main = lambda argv: print(resource.getrlimit(resource.RLIMIT_AS)[0]) or 0\n"
                 "sys.argv = [%r]\n"
                 "runpy.run_path(%r, run_name='__main__')\n" % (LAUNCHER, LAUNCHER))
        done = subprocess.run([sys.executable, "-B", "-c", probe], capture_output=True, timeout=60,
                              env=dict(os.environ, PYTHONPATH=support.BACKEND))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(int(done.stdout), cli.MEMORY_LIMIT)

    def test_a_package_file_of_nothing_but_tags_is_set_aside(self):
        # A few kilobytes of zip that would ask for gigabytes of tree. The
        # package file is treated as damaged; the chapters are still found.
        epubkit.make(self.books, "bomb.epub", manifest_extra="<item/>" * 1_200_000)
        self.assertLess(os.path.getsize(os.path.join(self.books, "bomb.epub")), 100_000)
        started = time.monotonic()
        done = self.launch("open", "--cache", self.cache, "--", os.path.join(self.books, "bomb.epub"))
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(done.stdout.count(b"\n"), 1)
        answer = json.loads(done.stdout)
        self.assertTrue(answer["ok"], answer)
        with open(answer["book"], encoding="utf-8") as handle:
            self.assertEqual([block["t"] for block in json.load(handle)["blocks"]], ["One", "alpha", "Two", "beta"])
        listed = json.loads(self.launch("scan", "--cache", self.cache, "--dir", self.books).stdout)
        self.assertEqual(len(listed["books"]), 1)

    def test_nothing_is_written_beside_the_program(self):
        epubkit.make(self.books)
        self.launch("scan", "--cache", self.cache, "--dir", self.books)
        stray = [os.path.join(base, name) for base, folders, _ in os.walk(support.ROOT)
                 for name in folders if name == "__pycache__" and os.sep + ".git" + os.sep not in base]
        self.assertEqual(stray, [])


if __name__ == "__main__":
    unittest.main()
