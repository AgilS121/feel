<?php
// PHP + PDO: N+1 pattern — emulates Laravel without ->with() eager loading.
// 1 query for orders + 100 queries for each related table  = 401 queries total.
header('Content-Type: application/json');

if ($_SERVER['REQUEST_URI'] !== '/orders') {
    http_response_code(404);
    exit;
}

$dbPath = __DIR__ . '/orders.db';
$pdo = new PDO("sqlite:$dbPath");
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

$orders = $pdo->query("SELECT * FROM orders ORDER BY id DESC LIMIT 100")->fetchAll(PDO::FETCH_ASSOC);

$userStmt     = $pdo->prepare("SELECT name FROM users     WHERE id = ?");
$customerStmt = $pdo->prepare("SELECT name FROM customers WHERE id = ?");
$contactStmt  = $pdo->prepare("SELECT name FROM contacts  WHERE id = ?");
$invoiceStmt  = $pdo->prepare("SELECT status FROM invoices WHERE id = ?");

$out = [];
foreach ($orders as $o) {
    $userStmt->execute([$o['user_id']]);
    $customerStmt->execute([$o['customer_id']]);
    $contactStmt->execute([$o['contact_id']]);
    $invoiceStmt->execute([$o['invoice_id']]);
    $out[] = [
        'id'             => $o['id'],
        'total'          => $o['total'],
        'created_at'     => $o['created_at'],
        'user_name'      => $userStmt->fetchColumn(),
        'customer_name'  => $customerStmt->fetchColumn(),
        'contact_name'   => $contactStmt->fetchColumn(),
        'invoice_status' => $invoiceStmt->fetchColumn(),
    ];
}
echo json_encode($out);
