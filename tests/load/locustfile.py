"""
Locust Load Testing for Aelira API.

Simulates 50 universities with 500 faculty users accessing the platform.

Usage:
    # Install locust
    pip install locust

    # Run load test (web UI)
    locust -f tests/load/locustfile.py --host=http://localhost:8000

    # Run headless with specific user count
    locust -f tests/load/locustfile.py --host=http://localhost:8000 \
        --users=500 --spawn-rate=10 --run-time=5m --headless

Target Metrics:
    - 500 concurrent users
    - < 500ms p95 response time
    - < 1% error rate
    - Sustained load for 10 minutes
"""

import random
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner

# Test data
DEPARTMENTS = [f"dept-{i:03d}" for i in range(50)]  # 50 universities
FACULTY_PER_DEPT = 10  # 10 faculty per department = 500 total

# Simulated file types for scanning
FILE_TYPES = ["pdf", "pptx", "docx", "html", "latex"]

# Sample LaTeX for testing
SAMPLE_LATEX = r"""
\documentclass{article}
\begin{document}
$x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$
\end{document}
"""

# Sample HTML for testing
SAMPLE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><title>Test</title></head>
<body>
    <img src="test.jpg">
    <button>Click me</button>
</body>
</html>
"""


class FacultyUser(HttpUser):
    """
    Simulates a faculty member using the Aelira platform.

    Typical workflow:
    1. Check health/status endpoints
    2. List their scans
    3. Upload and scan documents
    4. View scan results
    5. Check compliance dashboard
    """

    # Wait 1-5 seconds between tasks (realistic user behavior)
    wait_time = between(1, 5)

    def on_start(self):
        """Called when a user starts. Set up user context."""
        self.department_id = random.choice(DEPARTMENTS)
        self.user_id = f"faculty-{random.randint(1, 1000)}"
        self.api_key = f"test-api-key-{self.user_id}"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    # =========================================================================
    # Health & Status (Frequent, Lightweight)
    # =========================================================================

    @task(10)
    def health_check(self):
        """Check API health (very common)."""
        self.client.get("/health")

    @task(5)
    def education_health(self):
        """Check education API health including AI models."""
        self.client.get("/education/health")

    # =========================================================================
    # Scan History (Common Read Operations)
    # =========================================================================

    @task(8)
    def list_scans(self):
        """List scan history for department."""
        self.client.get(
            "/education/scans",
            params={"department_id": self.department_id, "limit": 20},
            headers=self.headers,
        )

    @task(3)
    def get_scan_details(self):
        """Get details of a specific scan (simulated ID)."""
        scan_id = f"scan-{random.randint(1, 1000)}"
        with self.client.get(
            f"/education/scans/{scan_id}",
            headers=self.headers,
            catch_response=True,
        ) as response:
            # 404 is expected for random IDs - don't count as failure
            if response.status_code in [200, 404]:
                response.success()

    # =========================================================================
    # Document Scanning (Heavy Operations)
    # =========================================================================

    @task(2)
    def scan_html_code(self):
        """Upload and scan HTML code."""
        files = {"file": ("test.html", SAMPLE_HTML.encode(), "text/html")}
        self.client.post(
            "/education/code/scan",
            files=files,
            headers=self.headers,
        )

    @task(1)
    def convert_latex(self):
        """Convert LaTeX to MathML."""
        files = {"file": ("test.tex", SAMPLE_LATEX.encode(), "text/x-tex")}
        self.client.post(
            "/education/latex/convert",
            files=files,
            headers=self.headers,
        )

    # =========================================================================
    # Analytics & Dashboard (Medium Frequency)
    # =========================================================================

    @task(4)
    def compliance_dashboard(self):
        """View compliance dashboard."""
        self.client.get(
            f"/api/analytics/dashboard/{self.department_id}",
            headers=self.headers,
        )

    @task(2)
    def compliance_trends(self):
        """Get compliance trends over time."""
        self.client.get(
            f"/api/analytics/trends/{self.department_id}",
            params={"days": 30},
            headers=self.headers,
        )

    # =========================================================================
    # Integration Status (Occasional)
    # =========================================================================

    @task(2)
    def check_integrations(self):
        """Check integration status (Google, Microsoft, Canvas, etc.)."""
        self.client.get(
            "/integrations/status",
            params={"department_id": self.department_id},
            headers=self.headers,
        )


class AdminUser(HttpUser):
    """
    Simulates a department admin user.

    Admins perform more administrative tasks like:
    - User management
    - Bulk operations
    - Report generation
    """

    wait_time = between(2, 10)
    weight = 1  # 1 admin per 10 faculty users

    def on_start(self):
        """Set up admin context."""
        self.department_id = random.choice(DEPARTMENTS)
        self.user_id = f"admin-{self.department_id}"
        self.api_key = f"admin-api-key-{self.user_id}"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}

    @task(5)
    def list_department_users(self):
        """List users in department."""
        self.client.get(
            "/api/admin/users",
            params={"department_id": self.department_id},
            headers=self.headers,
        )

    @task(3)
    def get_department_stats(self):
        """Get department usage statistics."""
        self.client.get(
            "/api/admin/stats",
            params={"department_id": self.department_id},
            headers=self.headers,
        )

    @task(2)
    def generate_compliance_report(self):
        """Generate compliance report (heavier operation)."""
        with self.client.get(
            f"/api/analytics/report/{self.department_id}",
            headers=self.headers,
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()


# =========================================================================
# Event Handlers
# =========================================================================


@events.init.add_listener
def on_locust_init(environment, **kwargs):
    """Initialize test environment."""
    print(f"\n{'='*60}")
    print("Aelira Load Test Starting")
    print(f"Target: {environment.host}")
    print("Simulating: 50 universities, 500 faculty users")
    print(f"{'='*60}\n")


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    print("Test started!")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops."""
    print("\nTest completed!")

    if isinstance(environment.runner, MasterRunner):
        # Print summary for distributed runs
        print(f"Total users spawned: {environment.runner.user_count}")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """Track individual requests for debugging slow endpoints."""
    if response_time > 1000:  # Log requests taking > 1 second
        print(f"SLOW: {name} - {response_time:.0f}ms")
