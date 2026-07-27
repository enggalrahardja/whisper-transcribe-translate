import Link from "next/link";
import { DashboardOverview } from "./components/dashboard-overview";
import { apiBaseUrl, Job, JobSummary } from "./lib/api";

const services = [
  { title: "Transcribe Audio", description: "Upload audio or video and generate editable text.", href: "/transcribe" },
  { title: "Translate Audio", description: "Transcribe and translate media into another language.", href: "/translate" },
  { title: "Live Transcription", description: "Capture microphone audio and display live transcript chunks.", href: "/live" },
  { title: "Subtitle Editor", description: "Review timestamps and export subtitle files.", href: "/subtitle" },
];

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
    <DashboardOverview initialJobs={jobs} initialSummary={summary} initiallyConnected={connected}>
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
              <Link href={service.href}>Open</Link>
            </article>
          ))}
        </div>
      </section>
    </DashboardOverview>
  );
}
