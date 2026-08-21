const ICP_RECORD_URL = "https://beian.miit.gov.cn/";

export function ComplianceFooter() {
  return (
    <footer className="compliance-footer">
      <a href={ICP_RECORD_URL} target="_blank" rel="noopener noreferrer">
        鄂ICP备2026044999号
      </a>
    </footer>
  );
}
