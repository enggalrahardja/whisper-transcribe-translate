type HistoryLoadingProps = {
  label: string;
};

export function HistoryLoading({ label }: HistoryLoadingProps) {
  return (
    <div aria-live="polite" className="history-loading" role="status">
      <div className="history-loading-heading">
        <span aria-hidden="true" className="history-loading-spinner" />
        <div>
          <strong>{label}</strong>
          <span>Fetching the latest records…</span>
        </div>
      </div>
      <div aria-hidden="true" className="history-loading-rows">
        {[0, 1, 2].map((row) => (
          <div className="history-loading-row" key={row}>
            <span /><span /><span /><span /><span />
          </div>
        ))}
      </div>
    </div>
  );
}
