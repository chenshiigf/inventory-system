"""Request-local monotonic timings, with no images, credentials or product data."""
from time import perf_counter_ns


class SearchTiming:
    def __init__(self):
        self.started = perf_counter_ns()
        self.previous = self.started
        self.parts = {}

    def mark(self, name):
        current = perf_counter_ns()
        self.parts[name] = (current - self.previous) / 1_000_000
        self.previous = current

    def result(self):
        return {**{k: round(v, 3) for k, v in self.parts.items()},
                "total": round((perf_counter_ns() - self.started) / 1_000_000, 3)}
