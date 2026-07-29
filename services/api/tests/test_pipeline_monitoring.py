import unittest
from app.services.pipeline_monitoring import latency_summary, percentile, quality_indicators, redact_metrics, warnings_for

class MonitoringTests(unittest.TestCase):
    def test_percentiles(self):
        self.assertEqual(percentile([1,2,3,4],50),2.5); self.assertEqual(latency_summary([1,2,100])["p95Ms"],90.2)
    def test_empty_percentile(self): self.assertEqual(percentile([],95),0)
    def test_redaction(self):
        value=redact_metrics({"sessionId":"x","transcript":"secret","token":"key","queueDepth":2})
        self.assertEqual(value,{"queueDepth":2})
    def test_worker_unavailable_and_thresholds(self):
        warnings=warnings_for({"live":{"ready":False,"capacity":10,"queueDepth":9,"averageProcessingMs":6000,"failed":2,"completed":1}})
        self.assertEqual({item["code"] for item in warnings},{"worker_unavailable","queue_utilization","processing_latency","high_failure_rate"})
    def test_degraded_persistence(self): self.assertEqual(warnings_for({},persistence_degraded=1)[0]["code"],"persistence_degraded")
    def test_quality_rates(self): self.assertEqual(quality_indicators({"segmentCount":2,"untranslatedSegments":1})["untranslatedSegmentRate"],.5)
    def test_session_isolation_and_no_content(self):
        self.assertNotIn("text", redact_metrics({"text":"hello","metrics":{"completed":1}})); self.assertEqual(redact_metrics({"metrics":{"completed":1}})["metrics"]["completed"],1)

if __name__ == "__main__": unittest.main()
