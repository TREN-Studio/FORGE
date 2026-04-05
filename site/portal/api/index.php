<?php
header('Cache-Control: no-store');

$request_uri = $_SERVER['REQUEST_URI'] ?? '/';
$script_name = $_SERVER['SCRIPT_NAME'] ?? '';
$query_string = $_SERVER['QUERY_STRING'] ?? '';
$path = $request_uri;

if ($query_string !== '') {
    $path = str_replace('?' . $query_string, '', $path);
}

if (!empty($script_name) && str_starts_with($path, $script_name)) {
    $path = substr($path, strlen($script_name));
}

if ($path === '' || $path === false) {
    $path = '/';
}

$domain_root = dirname(dirname(dirname(dirname(__DIR__))));
$backend_root = $domain_root . DIRECTORY_SEPARATOR . 'private' . DIRECTORY_SEPARATOR . 'forge_portal';
$script = $backend_root . DIRECTORY_SEPARATOR . 'portal_entry.py';

$python_candidates = [
    '/usr/bin/python3',
    '/opt/alt/python39/bin/python3',
    '/usr/local/bin/python3',
    'python3',
];

$python_cmd = null;
foreach ($python_candidates as $candidate) {
    if ($candidate === 'python3') {
        $python_cmd = $candidate;
        break;
    }
    if (file_exists($candidate)) {
        $python_cmd = $candidate;
        break;
    }
}

if (!$python_cmd || !file_exists($script)) {
    http_response_code(500);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode([
        'error' => 'Portal backend is not deployed correctly.',
        'details' => [
            'python' => $python_cmd,
            'script_exists' => file_exists($script),
        ],
    ]);
    exit;
}

$descriptors = [
    0 => ['pipe', 'r'],
    1 => ['pipe', 'w'],
    2 => ['pipe', 'w'],
];

$env = array_merge($_SERVER, [
    'FORGE_PORTAL_REQUEST_METHOD' => $_SERVER['REQUEST_METHOD'] ?? 'GET',
    'FORGE_PORTAL_REQUEST_PATH' => $path,
    'FORGE_PORTAL_STATE_ROOT' => $backend_root . DIRECTORY_SEPARATOR . 'state',
    'FORGE_PORTAL_COOKIE_PATH' => '/FORGE/portal',
    'FORGE_PORTAL_MANAGER_EMAIL' => 'larbilife@gmail.com',
    'PYTHONIOENCODING' => 'utf-8',
]);

$process = proc_open([$python_cmd, $script], $descriptors, $pipes, $backend_root, $env);
if (!is_resource($process)) {
    http_response_code(500);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['error' => 'Failed to start the portal backend process.']);
    exit;
}

$input = file_get_contents('php://input');
if ($input !== false && $input !== '') {
    fwrite($pipes[0], $input);
}
fclose($pipes[0]);

$stdout = stream_get_contents($pipes[1]);
$stderr = stream_get_contents($pipes[2]);
fclose($pipes[1]);
fclose($pipes[2]);

$exit_code = proc_close($process);
$payload = json_decode($stdout ?: '{}', true);

if (!is_array($payload) || $exit_code !== 0) {
    http_response_code(500);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode([
        'error' => 'Portal backend execution failed.',
        'details' => trim((string)$stderr),
        'stdout' => trim((string)$stdout),
    ]);
    exit;
}

http_response_code((int)($payload['status'] ?? 200));
$headers = $payload['headers'] ?? [];
if (is_array($headers)) {
    foreach ($headers as $name => $value) {
        if (is_string($name) && is_string($value) && $name !== '') {
            header($name . ': ' . $value, false);
        }
    }
}
echo (string)($payload['body'] ?? '');
