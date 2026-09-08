import asyncio
import os
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from matoloop_agent.terminal import PTY_AVAILABLE


@unittest.skipUnless(PTY_AVAILABLE and shutil.which("gcc"), "Native Bash build required")
class NativeSharedTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.library = Path(cls.build.name) / "shared.so"
        subprocess.run(
            ["sh", str(Path(__file__).parents[1] / "build-native.sh"), str(cls.library)],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent, child = socket.socketpair()
        self.parent.setblocking(False)
        child.set_inheritable(True)
        self.addCleanup(self.parent.close)
        self.addCleanup(child.close)
        rc = Path(self.temp.name) / "bashrc"
        rc.write_text(
            f"enable -f {shlex.quote(str(self.library))} ml_shared || exit\n"
            f"ml_shared init {child.fileno()} || exit\n"
            "PS1='READY> '; set +H\n"
            "PROMPT_COMMAND='builtin ml_shared prompt \"$?\"'\n"
        )
        import pty

        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            self.parent.close()
            os.chdir(self.temp.name)
            os.execve(
                "/bin/bash",
                ["bash", "--noprofile", "--rcfile", str(rc), "-i"],
                {"PATH": "/usr/bin:/bin", "TERM": "xterm", "INPUTRC": "/dev/null"},
            )
        child.close()
        self.output = bytearray()
        self.buffer = bytearray()
        self.sequence = 0
        self.status = None
        self.addAsyncCleanup(self.close)
        await self.until(b"READY> ")
        await self.rpc("status")

    async def close(self):
        os.killpg(self.pid, signal.SIGKILL)
        os.close(self.fd)
        await asyncio.to_thread(os.waitpid, self.pid, 0)

    async def until(self, marker):
        async with asyncio.timeout(3):
            while marker not in self.output:
                if select.select([self.fd], [], [], 0)[0]:
                    self.output.extend(os.read(self.fd, 65536))
                else:
                    await asyncio.sleep(0.005)

    async def rpc(self, operation, command="", expected=None):
        self.sequence += 1
        seq = str(self.sequence)
        status = expected or self.status
        fields = [
            seq,
            operation,
            "proposal",
            command,
            status[7] if status else "0",
            status[4] if status else "0",
            str(time.monotonic() + 2),
        ]
        packet = ("\0".join(fields) + "\0").encode()
        await asyncio.get_running_loop().sock_sendall(
            self.parent, struct.pack("!I", len(packet)) + packet
        )
        async with asyncio.timeout(3):
            while True:
                while len(self.buffer) >= 4:
                    size = struct.unpack("!I", self.buffer[:4])[0]
                    if len(self.buffer) < size + 4:
                        break
                    data = bytes(self.buffer[4 : 4 + size]).decode().split("\0")[:-1]
                    del self.buffer[: 4 + size]
                    self.status = data
                    if data[0] == seq:
                        return data
                chunk = await asyncio.get_running_loop().sock_recv(self.parent, 65536)
                if not chunk:
                    self.fail(f"control channel closed: {self.output!r}")
                self.buffer.extend(chunk)

    async def test_native_gate_races_and_personal_typeahead(self):
        os.write(self.fd, b"printf '__FIRST__%s\\n' yes\rprintf '__SECOND__%s\\n' yes\r")
        await self.until(b"__SECOND__yes")
        self.assertIn(b"__FIRST__yes", self.output)
        await self.rpc("status")
        for _ in range(3):
            self.assertEqual((await self.rpc("stage", "touch never"))[1], "")
            os.write(self.fd, b"\r")
            os.write(self.fd, b"\x03")
            status = await self.rpc("status")
            for _ in range(10):
                if status[2] == "idle":
                    break
                await asyncio.sleep(0.01)
                status = await self.rpc("status")
            if select.select([self.fd], [], [], 0)[0]:
                self.output.extend(os.read(self.fd, 65536))
            self.assertEqual(status[2], "idle", repr(self.output))
            self.assertEqual(status[9], "1")
            self.assertFalse((Path(self.temp.name) / "never").exists())
        await self.rpc("stage", "printf '__VALUE__%s\\n' old")
        os.write(self.fd, b"\x17edited\r")
        inspected = await self.rpc("inspect")
        self.assertEqual(inspected[2], "checking")
        self.assertEqual(inspected[8], "1")
        self.assertIn("edited", inspected[6])
        os.write(self.fd, b"UNSEEN\r")
        self.assertEqual((await self.rpc("execute", "wrong", inspected))[1], "terminal.stale")
        self.assertEqual((await self.rpc("execute", inspected[6], inspected))[2], "running")
        await self.until(b"__VALUE__edited")

    async def test_expansion_completion_and_partial_line(self):
        os.write(self.fd, b"partial")
        self.assertEqual((await self.rpc("stage", "touch never"))[1], "terminal.busy")
        os.write(self.fd, b"\x03")
        await self.rpc("status")
        await self.rpc("stage", "printf '%s' \"$(touch never)\"")
        os.write(self.fd, b"\x1b\x05\t\x1b?\x18\x05")
        await self.rpc("status")
        self.assertFalse((Path(self.temp.name) / "never").exists())
        self.assertEqual((await self.rpc("cancel"))[2], "idle")
