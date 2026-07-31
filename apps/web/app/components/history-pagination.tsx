"use client";

type HistoryPaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
};

export function HistoryPagination({
  page,
  pageSize,
  total,
  totalPages,
  onPageChange,
  onPageSizeChange,
}: HistoryPaginationProps) {
  const firstRow = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastRow = Math.min(total, page * pageSize);
  const visiblePageCount = Math.min(7, totalPages);
  const firstVisiblePage = Math.max(1, Math.min(page - Math.floor(visiblePageCount / 2), totalPages - visiblePageCount + 1));
  const visiblePages = Array.from({ length: visiblePageCount }, (_, index) => firstVisiblePage + index);
  return (
    <div className="history-pagination">
      <div className="history-pagination-meta">
        <label>
          Rows per page
          <select onChange={(event) => onPageSizeChange(Number(event.target.value))} value={pageSize}>
            {[5, 10, 20, 50].map((size) => <option key={size} value={size}>{size}</option>)}
          </select>
        </label>
        <span>{firstRow}–{lastRow} of {total}</span>
      </div>
      <nav aria-label="History pagination" className="history-page-navigation">
        <button disabled={page === 1} onClick={() => onPageChange(1)} type="button">First</button>
        <button disabled={page === 1} onClick={() => onPageChange(Math.max(1, page - 1))} type="button">Prev</button>
        {visiblePages.map((pageNumber) => (
          <button
            aria-current={pageNumber === page ? "page" : undefined}
            className={pageNumber === page ? "active" : undefined}
            key={pageNumber}
            onClick={() => onPageChange(pageNumber)}
            type="button"
          >
            {pageNumber}
          </button>
        ))}
        <button disabled={page === totalPages} onClick={() => onPageChange(Math.min(totalPages, page + 1))} type="button">Next</button>
        <button disabled={page === totalPages} onClick={() => onPageChange(totalPages)} type="button">Last</button>
      </nav>
    </div>
  );
}
