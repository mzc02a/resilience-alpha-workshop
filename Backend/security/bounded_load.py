"""Shared fixed-ceiling workload: <=5 starts/s, <=4 in flight, <=180 seconds."""
import concurrent.futures
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run_load(base_url, token, seconds, rps):
    if not (15 <= seconds <= 180 and 1 <= rps <= 5):
        raise ValueError("Invalid bounded workload")
    def one():
        started = time.time()
        req = urllib.request.Request(base_url.rstrip("/") + "/payments", headers={"Authorization": "Bearer " + token})
        try:
            direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with direct.open(req, timeout=3) as response:
                response.read(1048576)
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
        except (urllib.error.URLError, OSError):
            status = "unavailable"
        return {"timestamp": started, "status": status, "duration_seconds": time.time() - started}
    results = []
    pending = set()
    start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        try:
            for tick in range(seconds * rps):
                delay = start + tick / rps - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if time.monotonic() >= start + seconds:
                    break
                done = {future for future in pending if future.done()}
                results.extend(future.result() for future in done)
                pending -= done
                if len(pending) < 4:
                    pending.add(pool.submit(one))
        except KeyboardInterrupt:
            pass
        results.extend(future.result() for future in pending)
    return results
