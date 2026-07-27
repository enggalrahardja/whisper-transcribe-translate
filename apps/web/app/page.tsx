const services = [
  { title: "Transcribe Audio", description: "Upload audio or video and generate editable text." },
  { title: "Translate Audio", description: "Transcribe and translate media into another language." },
  { title: "Live Transcription", description: "Capture microphone audio and display live transcript chunks." },
  { title: "Subtitle Editor", description: "Review timestamps and export subtitle files." },
];

type Job = {
  id: string;
  file_name: string;
  task: string;
  model: string;
  status: string;
  progress: number;
  created_at: string;
};

type JobSummary = {
  total: number;
  completed: number;
  processing: number;
  failed: number;
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function loadDashboardData(): Promise<{ jobs: Job[]; summary: JobSummary; connected: boolean }> {
  try {
    const [jobsResponse, summaryResponse] = await Promise.all([
      fetch(`${apiBaseUrl}/api/jobs?limit=5`, { cache: "no-store" }),
      fetch(`${apiBaseUrl}/api/jobs/summary`, { cache: "no-store" }),
    ]);

    if (!jobsResponse.ok || !summaryResponse.ok) {
      throw new Error("API request failed");
    }

    return {
      jobs: await jobsResponse.json(),
      summary: await summaryResponse.json(),
      connected: true,
    };
  } catch {
    return {
      jobs: [],
      summary: { total: 0, completed: 0, processing: 0, failed: 0 },
      connected: false,
    };
  }
}

export default async function HomePage() {
  const { jobs, summary, connected } = await loadDashboardData();

  return (
    <main className="shell">
      <aside className="sidebar">
        <div>
          <div className="brand-mark">W</div>
          <div className="brand-copy">
            <strong>Whisper</strong>
            <span>Transcribe & Translate</span>
          </div>
        </div>

        <nav>
          {["Dashboard", "Transcribe Audio", "Translate Audio", "Live Transcription", "Subtitle Editor", "History", "Settings"].map(
            (item, index) => (
              <button className={index === 0 ? "active" : ""} key={item} type="button">
                {item}
              </button>
            ),
          )}
        </nav>
      </aside>

      <section className="content">
        <header>
          <div>
            <p className="eyebrow">ONLINE WORKSPACE</p>
            <h1>Dashboard</h1>
            <p>Manage transcription, translation, and subtitle processing from one place.</p>
          </div>
          <span className="status">{connected ? "API & MongoDB connected" : "API unavailable"}</span>
        </header>

        <section className="stats">
          <article><span>Total Jobs</span><strong>{summary.total}</strong></article>
          <article><span>Completed</span><strong>{summary.completed}</strong></article>
          <article><span>Processing</span><strong>{summary.processing}</strong></article>
          <article><span>Failed</span><strong>{summary.failed}</strong></article>
        </section>

        <section>
          <div className="section-heading">
            <div>
              <p className="eyebrow">QUICK ACTIONS</p>
              <h2>Start a workflow</h2>
            </div>
          </div>
          <div className="service-grid">
            {services.map((service, index) => (
              <article className="service-card" key={service.title}>
                <span className="service-number">0{index + 1}</span>
                <h3>{service.title}</h3>
                <p>{service.description}</p>
                <button type="button">Open</button>
              </article>
            ))}
          </div>
        </section>

        <section className="jobs-panel">
          <div>
            <p className="eyebrow">RECENT JOBS</p>
            {jobs.length === 0 ? (
              <>
                <h2>No processing jobs yet</h2>
                <p>Uploaded media and processing progress will appear here.</p>
              </>
            ) : (
              <div className="job-list">
                {jobs.map((job) => (
                  <article className="job-row" key={job.id}>
                    <div>
                      <strong>{job.file_name}</strong>
                      <span>{job.task} · {job.model}</span>
                    </div>
                    <div>
                      <strong>{job.status}</strong>
                      <span>{job.progress}%</span>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
        </section>
      </section>
    </main>
  );
}
