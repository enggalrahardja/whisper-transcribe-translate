const services = [
  { title: "Transcribe Audio", description: "Upload audio or video and generate editable text." },
  { title: "Translate Audio", description: "Transcribe and translate media into another language." },
  { title: "Live Transcription", description: "Capture microphone audio and display live transcript chunks." },
  { title: "Subtitle Editor", description: "Review timestamps and export subtitle files." },
];

export default function HomePage() {
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
          {[
            "Dashboard",
            "Transcribe Audio",
            "Translate Audio",
            "Live Transcription",
            "Subtitle Editor",
            "History",
            "Settings",
          ].map((item, index) => (
            <button className={index === 0 ? "active" : ""} key={item} type="button">
              {item}
            </button>
          ))}
        </nav>
      </aside>

      <section className="content">
        <header>
          <div>
            <p className="eyebrow">ONLINE WORKSPACE</p>
            <h1>Dashboard</h1>
            <p>Manage transcription, translation, and subtitle processing from one place.</p>
          </div>
          <span className="status">Foundation initialized</span>
        </header>

        <section className="stats">
          <article><span>Total Jobs</span><strong>0</strong></article>
          <article><span>Completed</span><strong>0</strong></article>
          <article><span>Processing</span><strong>0</strong></article>
          <article><span>Failed</span><strong>0</strong></article>
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
            <h2>No processing jobs yet</h2>
            <p>Uploaded media and processing progress will appear here.</p>
          </div>
        </section>
      </section>
    </main>
  );
}
