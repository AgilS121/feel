<?php
// PHP HTTP hello server (benchmark)
// Run via:  php -S 127.0.0.1:3005 hello.php
header('Content-Type: application/json');
if ($_SERVER['REQUEST_URI'] === '/hello') {
    echo '"Hello, World"';
} else {
    http_response_code(404);
}
