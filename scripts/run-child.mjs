export function runChild(child, name) {
  let stopping = false;

  const forward = (signal) => {
    if (stopping) return;
    stopping = true;
    if (child.exitCode === null) child.kill(signal);
  };

  process.on("SIGINT", () => forward("SIGINT"));
  process.on("SIGTERM", () => forward("SIGTERM"));
  child.on("error", (error) => {
    console.error(`${name} failed to start: ${error.message}`);
    process.exitCode = 1;
  });
  child.on("exit", (code, signal) => {
    if (signal && !stopping) console.error(`${name} exited from signal ${signal}`);
    process.exit(code ?? (stopping ? 0 : 1));
  });
}
