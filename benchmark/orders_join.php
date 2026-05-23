<?php
// PHP + PDO: 5-table JOIN (the "right way" — emulates Laravel with eager load done correctly).
header('Content-Type: application/json');

if ($_SERVER['REQUEST_URI'] !== '/orders') {
    http_response_code(404);
    exit;
}

$dbPath = __DIR__ . '/orders.db';
$pdo = new PDO("sqlite:$dbPath");
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

$sql = "
  SELECT
    o.id, o.total, o.created_at,
    u.name AS user_name,
    c.name AS customer_name,
    ct.name AS contact_name,
    i.status AS invoice_status
  FROM orders o
  JOIN users u      ON u.id = o.user_id
  JOIN customers c  ON c.id = o.customer_id
  JOIN contacts ct  ON ct.id = o.contact_id
  JOIN invoices i   ON i.id = o.invoice_id
  ORDER BY o.id DESC
  LIMIT 100
";

$rows = $pdo->query($sql)->fetchAll(PDO::FETCH_ASSOC);
echo json_encode($rows);
