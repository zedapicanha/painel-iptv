from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify, Response
import requests
import re
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict
import uuid
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()

# Configurable URLs
PUBLIC_URL = "http://jontexplay.duckdns.org"
PUBLIC_URL2 = "http://backup1.jontexplay.serveo.net"
PUBLIC_URL3 = "http://backup2.jontexplay.serveo.net"

# Source M3U URL (replace with valid credentials)
M3U_URL = "http://megaplay.uno/get.php?username=886337400&password=305305362&type=m3u_plus&output=mpegts"

# Database files
USERS_DB_FILE = "users_db.json"
CLIENTS_DB_FILE = "clients_db.json"
RESELLERS_DB_FILE = "resellers_db.json"
LAYOUT_SETTINGS_FILE = "layout_settings.json"

# Cache for M3U list
channels_cache = {"data": [], "last_updated": None}
CACHE_TIMEOUT = timedelta(hours=1)

# Maintenance mode
MAINTENANCE_MODE = False

def load_db(file_path: str, default: Dict = None) -> Dict:
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                data = json.load(f)
                if file_path == USERS_DB_FILE:
                    for username in data:
                        data[username].setdefault("credits", float('inf') if username == "admin" else 100)
                        data[username].setdefault("infinite_credits", username == "admin")
                        data[username].setdefault("email", "")
                if file_path == RESELLERS_DB_FILE:
                    for reseller in data.values():
                        reseller.setdefault("infinite_credits", False)
                        reseller.setdefault("email", "")
                        reseller.setdefault("clients", [])
                        reseller.setdefault("sub_resellers", [])
                if file_path == LAYOUT_SETTINGS_FILE:
                    data.setdefault("client_info_template", "Usuário: #user_iptv#\nSenha: #pass_iptv#\nM3U: #url_m3u#\nDNS1: #dns_iptv#\nDNS2: #dns_iptv2#\nDNS3: #dns_iptv3#")
                    data.setdefault("public_url2", PUBLIC_URL2)
                    data.setdefault("public_url3", PUBLIC_URL3)
                return data
        except json.JSONDecodeError:
            print(f"Error decoding {file_path}. Using default.")
            return default or {}
    return default or {}

def save_db(data: Dict, file_path: str):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=4)

users_db = load_db(USERS_DB_FILE, {"admin": {"password": "admin123", "role": "admin", "credits": float('inf'), "infinite_credits": True, "email": ""}})
clients_db = load_db(CLIENTS_DB_FILE, {})
resellers_db = load_db(RESELLERS_DB_FILE, {})
layout_settings_db = load_db(LAYOUT_SETTINGS_FILE, {
    "header_color": "bg-blue-500",
    "header_position": "fixed w-full top-0 z-50",
    "welcome_text": "Bem-vindo, {{ username }}!",
    "login_image_url": "",
    "login_image_width": "100",
    "login_image_height": "100",
    "login_image_position": "center",
    "client_info_template": "Usuário: #user_iptv#\nSenha: #pass_iptv#\nM3U: #url_m3u#\nDNS1: #dns_iptv#\nDNS2: #dns_iptv2#\nDNS3: #dns_iptv3#",
    "public_url2": PUBLIC_URL2,
    "public_url3": PUBLIC_URL3
})

def fetch_m3u(url: str) -> List[Dict]:
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        lines = response.text.splitlines()
        channels = []
        current_channel = {}
        channel_index = 0
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#EXTM3U"):
                continue
            if line.startswith("#EXTINF:"):
                match = re.search(r'tvg-id="([^"]*)"\s*tvg-name="([^"]*)"\s*tvg-logo="([^"]*)"\s*group-title="([^"]*)"\s*,(.+)', line)
                if not match:
                    match = re.search(r'-1\s+(?:tvg-id="([^"]*)"\s*)?(?:tvg-name="([^"]*)"\s*)?(?:tvg-logo="([^"]*)"\s*)?(?:group-title="([^"]*)"\s*)?,(.+)', line)
                if match:
                    tvg_id, name, logo, group, title = match.groups()
                    name = name or title or f"Channel_{channel_index}"
                    group = group or "Outros"
                    current_channel = {
                        "tvg_id": tvg_id or name,
                        "name": name.strip(),
                        "logo": logo or "",
                        "group": group,
                        "title": title.strip()
                    }
                    channel_index += 1
            elif line.startswith("http") and current_channel:
                current_channel["url"] = line
                channels.append(current_channel)
                current_channel = {}
        print(f"Fetched {len(channels)} channels from {url}")
        return channels
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch M3U: {str(e)}")
        return []

login_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #f3f4f6; }
    </style>
</head>
<body class="flex items-center justify-center min-h-screen">
    <div class="w-full max-w-md p-8 bg-white rounded-lg shadow-lg">
        {% if maintenance_mode and session.get('role') != 'admin' %}
            <div class="text-center">
                <h1 class="text-2xl font-bold text-red-600">Atenção ⚠️ Servidor em manutenção!</h1>
            </div>
        {% else %}
            {% if layout_settings.login_image_url %}
                <div class="flex justify-{{ layout_settings.login_image_position }} mb-6">
                    <img src="{{ layout_settings.login_image_url }}" alt="Logo" style="width: {{ layout_settings.login_image_width }}px; height: {{ layout_settings.login_image_height }}px;">
                </div>
            {% endif %}
            <h2 class="text-2xl font-bold text-center text-gray-800 mb-6">Login</h2>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <div class="mb-4 p-4 bg-red-100 text-red-700 rounded">
                        {{ messages[0] }}
                    </div>
                {% endif %}
            {% endwith %}
            <form method="POST" action="{{ url_for('login') }}">
                <div class="mb-4">
                    <label class="block text-gray-700">Usuário</label>
                    <input type="text" name="username" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                </div>
                <div class="mb-6">
                    <label class="block text-gray-700">Senha</label>
                    <input type="password" name="password" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                </div>
                <button type="submit" class="w-full p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Entrar</button>
            </form>
        {% endif %}
    </div>
</body>
</html>
"""

dashboard_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dashboard - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .sidebar { transition: width 0.3s ease; }
        .sidebar-hidden { width: 0; overflow: hidden; }
        .sidebar-open { width: 250px; }
        .content-shift { margin-left: 250px; transition: margin-left 0.3s ease; }
        .content-full { margin-left: 0; }
    </style>
</head>
<body class="bg-gray-100 font-sans">
    <div class="flex h-screen">
        <!-- Sidebar -->
        <div id="sidebar" class="sidebar sidebar-hidden {{ layout_settings.header_color }} text-white fixed h-full">
            <div class="p-4">
                <h2 class="text-xl font-bold">Painel IPTV</h2>
                <button id="toggleSidebar" class="mt-2 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                    </svg>
                </button>
            </div>
            <div class="p-4">
                <p class="text-sm">Bem-vindo, {{ username }}!</p>
                <p class="text-sm">Créditos: {{ credits }}</p>
            </div>
            <nav class="mt-4">
                <a href="{{ url_for('dashboard') }}" class="block py-2 px-4 hover:bg-blue-700">Dashboard</a>
                <a href="{{ url_for('profile') }}" class="block py-2 px-4 hover:bg-blue-700">Perfil</a>
                <a href="{{ url_for('ger_clientes') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Clientes</a>
                <a href="{{ url_for('ger_resellers') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Revendas</a>
                {% if role == 'admin' %}
                    <a href="{{ url_for('ferramenta') }}" class="block py-2 px-4 hover:bg-blue-700">Ferramentas</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="block py-2 px-4 hover:bg-blue-700">Sair</a>
            </nav>
        </div>
        <!-- Main Content -->
        <div id="content" class="flex-1 content-full p-6">
            <button id="openSidebar" class="mb-4 focus:outline-none">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                </svg>
            </button>
            <h1 class="text-2xl font-bold mb-6">{{ layout_settings.welcome_text }}</h1>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div class="bg-white p-6 rounded-lg shadow">
                    <h2 class="text-xl font-semibold">Clientes</h2>
                    <p class="text-3xl">{{ clients_count }}</p>
                </div>
                <div class="bg-white p-6 rounded-lg shadow">
                    <h2 class="text-xl font-semibold">Revendas</h2>
                    <p class="text-3xl">{{ resellers_count }}</p>
                </div>
            </div>
        </div>
    </div>
    <script>
        const sidebar = document.getElementById('sidebar');
        const content = document.getElementById('content');
        const toggleSidebar = document.getElementById('toggleSidebar');
        const openSidebar = document.getElementById('openSidebar');

        toggleSidebar.addEventListener('click', () => {
            sidebar.classList.toggle('sidebar-hidden');
            sidebar.classList.toggle('sidebar-open');
            content.classList.toggle('content-shift');
            content.classList.toggle('content-full');
        });

        openSidebar.addEventListener('click', () => {
            sidebar.classList.remove('sidebar-hidden');
            sidebar.classList.add('sidebar-open');
            content.classList.remove('content-full');
            content.classList.add('content-shift');
        });
    </script>
</body>
</html>
"""

profile_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Perfil - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .sidebar { transition: width 0.3s ease; }
        .sidebar-hidden { width: 0; overflow: hidden; }
        .sidebar-open { width: 250px; }
        .content-shift { margin-left: 250px; transition: margin-left 0.3s ease; }
        .content-full { margin-left: 0; }
    </style>
</head>
<body class="bg-gray-100 font-sans">
    <div class="flex h-screen">
        <!-- Sidebar -->
        <div id="sidebar" class="sidebar sidebar-hidden {{ layout_settings.header_color }} text-white fixed h-full">
            <div class="p-4">
                <h2 class="text-xl font-bold">Painel IPTV</h2>
                <button id="toggleSidebar" class="mt-2 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                    </svg>
                </button>
            </div>
            <div class="p-4">
                <p class="text-sm">Bem-vindo, {{ username }}!</p>
                <p class="text-sm">Créditos: {{ credits }}</p>
            </div>
            <nav class="mt-4">
                <a href="{{ url_for('dashboard') }}" class="block py-2 px-4 hover:bg-blue-700">Dashboard</a>
                <a href="{{ url_for('profile') }}" class="block py-2 px-4 hover:bg-blue-700">Perfil</a>
                <a href="{{ url_for('ger_clientes') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Clientes</a>
                <a href="{{ url_for('ger_resellers') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Revendas</a>
                {% if role == 'admin' %}
                    <a href="{{ url_for('ferramenta') }}" class="block py-2 px-4 hover:bg-blue-700">Ferramentas</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="block py-2 px-4 hover:bg-blue-700">Sair</a>
            </nav>
        </div>
        <!-- Main Content -->
        <div id="content" class="flex-1 content-full p-6">
            <button id="openSidebar" class="mb-4 focus:outline-none">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                </svg>
            </button>
            <h1 class="text-2xl font-bold mb-6">Perfil</h1>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <div class="mb-4 p-4 bg-green-100 text-green-700 rounded">
                        {{ messages[0] }}
                    </div>
                {% endif %}
            {% endwith %}
            <form method="POST" action="{{ url_for('profile') }}">
                <div class="mb-4">
                    <label class="block text-gray-700">Email</label>
                    <input type="text" name="email" value="{{ user_email }}" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700">Nova Senha</label>
                    <input type="password" name="password" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>
                <button type="submit" class="p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Salvar</button>
            </form>
        </div>
    </div>
    <script>
        const sidebar = document.getElementById('sidebar');
        const content = document.getElementById('content');
        const toggleSidebar = document.getElementById('toggleSidebar');
        const openSidebar = document.getElementById('openSidebar');

        toggleSidebar.addEventListener('click', () => {
            sidebar.classList.toggle('sidebar-hidden');
            sidebar.classList.toggle('sidebar-open');
            content.classList.toggle('content-shift');
            content.classList.toggle('content-full');
        });

        openSidebar.addEventListener('click', () => {
            sidebar.classList.remove('sidebar-hidden');
            sidebar.classList.add('sidebar-open');
            content.classList.remove('content-full');
            content.classList.add('content-shift');
        });
    </script>
</body>
</html>
"""

ger_clientes_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gerenciar Clientes - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .sidebar { transition: width 0.3s ease; }
        .sidebar-hidden { width: 0; overflow: hidden; }
        .sidebar-open { width: 250px; }
        .content-shift { margin-left: 250px; transition: margin-left 0.3s ease; }
        .content-full { margin-left: 0; }
    </style>
</head>
<body class="bg-gray-100 font-sans">
    <div class="flex h-screen">
        <!-- Sidebar -->
        <div id="sidebar" class="sidebar sidebar-hidden {{ layout_settings.header_color }} text-white fixed h-full">
            <div class="p-4">
                <h2 class="text-xl font-bold">Painel IPTV</h2>
                <button id="toggleSidebar" class="mt-2 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                    </svg>
                </button>
            </div>
            <div class="p-4">
                <p class="text-sm">Bem-vindo, {{ username }}!</p>
                <p class="text-sm">Créditos: {{ credits }}</p>
            </div>
            <nav class="mt-4">
                <a href="{{ url_for('dashboard') }}" class="block py-2 px-4 hover:bg-blue-700">Dashboard</a>
                <a href="{{ url_for('profile') }}" class="block py-2 px-4 hover:bg-blue-700">Perfil</a>
                <a href="{{ url_for('ger_clientes') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Clientes</a>
                <a href="{{ url_for('ger_resellers') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Revendas</a>
                {% if role == 'admin' %}
                    <a href="{{ url_for('ferramenta') }}" class="block py-2 px-4 hover:bg-blue-700">Ferramentas</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="block py-2 px-4 hover:bg-blue-700">Sair</a>
            </nav>
        </div>
        <!-- Main Content -->
        <div id="content" class="flex-1 content-full p-6">
            <button id="openSidebar" class="mb-4 focus:outline-none">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                </svg>
            </button>
            <h1 class="text-2xl font-bold mb-6">Gerenciar Clientes</h1>
            <div class="bg-white p-6 rounded-lg shadow mb-6">
                <h2 class="text-xl font-semibold mb-4">Criar Cliente</h2>
                {% with messages = get_flashed_messages() %}
                    {% if messages %}
                        <div class="mb-4 p-4 bg-green-100 text-green-700 rounded">
                            {{ messages[0] }}
                        </div>
                    {% endif %}
                {% endwith %}
                <form method="POST" action="{{ url_for('ger_clientes') }}">
                    <div class="mb-4">
                        <label class="block text-gray-700">Nome do Cliente</label>
                        <input type="text" name="client_name" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">Senha</label>
                        <input type="text" name="client_password" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">Conexões</label>
                        <div class="flex items-center">
                            <button type="button" onclick="document.getElementById('connections').value = Math.max(1, parseInt(document.getElementById('connections').value) - 1)" class="p-2 bg-gray-200 rounded-l">-</button>
                            <input type="number" id="connections" name="connections" value="1" min="1" class="w-16 p-2 border-t border-b text-center" readonly>
                            <button type="button" onclick="document.getElementById('connections').value = parseInt(document.getElementById('connections').value) + 1" class="p-2 bg-gray-200 rounded-r">+</button>
                        </div>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">Meses de Validade</label>
                        <div class="flex items-center">
                            <button type="button" onclick="document.getElementById('months').value = Math.max(1, parseInt(document.getElementById('months').value) - 1)" class="p-2 bg-gray-200 rounded-l">-</button>
                            <input type="number" id="months" name="months" value="1" min="1" class="w-16 p-2 border-t border-b text-center" readonly>
                            <button type="button" onclick="document.getElementById('months').value = parseInt(document.getElementById('months').value) + 1" class="p-2 bg-gray-200 rounded-r">+</button>
                        </div>
                    </div>
                    <button type="submit" class="p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Criar</button>
                </form>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Lista de Clientes</h2>
                <table class="w-full">
                    <thead>
                        <tr class="bg-gray-200">
                            <th class="p-2 text-left">Nome</th>
                            <th class="p-2 text-left">Senha</th>
                            <th class="p-2 text-left">Conexões</th>
                            <th class="p-2 text-left">Vencimento</th>
                            <th class="p-2 text-left">Status</th>
                            <th class="p-2 text-left">Ações</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for client_id, client in clients.items() if role == 'admin' or (session['username'] in resellers_db and client_id in resellers_db[session['username']].get('clients', [])) %}
                            <tr>
                                <td class="p-2">{{ client.name }}</td>
                                <td class="p-2">{{ client.password }}</td>
                                <td class="p-2">{{ client.connections }}</td>
                                <td class="p-2">{{ client.expiry_date }}</td>
                                <td class="p-2">{{ client.status }}</td>
                                <td class="p-2">
                                    <a href="{{ url_for('client_info', client_id=client_id) }}" class="text-blue-500 hover:underline">Ver Infos</a>
                                    <button onclick="toggleBlock('{{ client_id }}')" class="text-{{ 'red' if client.status == 'active' else 'green' }}-500 hover:underline">{{ 'Bloquear' if client.status == 'active' else 'Desbloquear' }}</button>
                                    <button onclick="deleteClient('{{ client_id }}')" class="text-red-500 hover:underline">Excluir</button>
                                </td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    <script>
        const sidebar = document.getElementById('sidebar');
        const content = document.getElementById('content');
        const toggleSidebar = document.getElementById('toggleSidebar');
        const openSidebar = document.getElementById('openSidebar');

        toggleSidebar.addEventListener('click', () => {
            sidebar.classList.toggle('sidebar-hidden');
            sidebar.classList.toggle('sidebar-open');
            content.classList.toggle('content-shift');
            content.classList.toggle('content-full');
        });

        openSidebar.addEventListener('click', () => {
            sidebar.classList.remove('sidebar-hidden');
            sidebar.classList.add('sidebar-open');
            content.classList.remove('content-full');
            content.classList.add('content-shift');
        });

        function toggleBlock(clientId) {
            fetch('/toggle_block/' + clientId, { method: 'POST' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    } else {
                        alert(data.message);
                    }
                });
        }

        function deleteClient(clientId) {
            if (confirm('Tem certeza que deseja excluir este cliente?')) {
                fetch('/delete_client/' + clientId, { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            location.reload();
                        } else {
                            alert(data.message);
                        }
                    });
            }
        }
    </script>
</body>
</html>
"""

ger_resellers_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gerenciar Revendas - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .sidebar { transition: width 0.3s ease; }
        .sidebar-hidden { width: 0; overflow: hidden; }
        .sidebar-open { width: 250px; }
        .content-shift { margin-left: 250px; transition: margin-left 0.3s ease; }
        .content-full { margin-left: 0; }
    </style>
</head>
<body class="bg-gray-100 font-sans">
    <div class="flex h-screen">
        <!-- Sidebar -->
        <div id="sidebar" class="sidebar sidebar-hidden {{ layout_settings.header_color }} text-white fixed h-full">
            <div class="p-4">
                <h2 class="text-xl font-bold">Painel IPTV</h2>
                <button id="toggleSidebar" class="mt-2 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                    </svg>
                </button>
            </div>
            <div class="p-4">
                <p class="text-sm">Bem-vindo, {{ username }}!</p>
                <p class="text-sm">Créditos: {{ credits }}</p>
            </div>
            <nav class="mt-4">
                <a href="{{ url_for('dashboard') }}" class="block py-2 px-4 hover:bg-blue-700">Dashboard</a>
                <a href="{{ url_for('profile') }}" class="block py-2 px-4 hover:bg-blue-700">Perfil</a>
                <a href="{{ url_for('ger_clientes') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Clientes</a>
                <a href="{{ url_for('ger_resellers') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Revendas</a>
                {% if role == 'admin' %}
                    <a href="{{ url_for('ferramenta') }}" class="block py-2 px-4 hover:bg-blue-700">Ferramentas</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="block py-2 px-4 hover:bg-blue-700">Sair</a>
            </nav>
        </div>
        <!-- Main Content -->
        <div id="content" class="flex-1 content-full p-6">
            <button id="openSidebar" class="mb-4 focus:outline-none">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                </svg>
            </button>
            <h1 class="text-2xl font-bold mb-6">Gerenciar Revendas</h1>
            <div class="bg-white p-6 rounded-lg shadow mb-6">
                <h2 class="text-xl font-semibold mb-4">Criar Revenda</h2>
                {% with messages = get_flashed_messages() %}
                    {% if messages %}
                        <div class="mb-4 p-4 bg-green-100 text-green-700 rounded">
                            {{ messages[0] }}
                        </div>
                    {% endif %}
                {% endwith %}
                <form method="POST" action="{{ url_for('ger_resellers') }}">
                    <div class="mb-4">
                        <label class="block text-gray-700">Nome da Revenda</label>
                        <input type="text" name="reseller_name" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">Senha</label>
                        <input type="text" name="reseller_password" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">Créditos</label>
                        <input type="number" name="credits" value="0" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500" required>
                    </div>
                    <button type="submit" class="p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Criar</button>
                </form>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Lista de Revendas</h2>
                <table class="w-full">
                    <thead>
                        <tr class="bg-gray-200">
                            <th class="p-2 text-left">Nome</th>
                            <th class="p-2 text-left">Créditos</th>
                            <th class="p-2 text-left">Ações</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for reseller_name, reseller in resellers.items() if role == 'admin' or (session['username'] in resellers_db and reseller_name in resellers_db[session['username']].get('sub_resellers', [])) %}
                            <tr>
                                <td class="p-2">{{ reseller_name }}</td>
                                <td class="p-2">{{ 'Infinitos' if reseller.infinite_credits else reseller.credits }}</td>
                                <td class="p-2">
                                    <button onclick="deleteReseller('{{ reseller_name }}')" class="text-red-500 hover:underline">Excluir</button>
                                </td>
                            </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    <script>
        const sidebar = document.getElementById('sidebar');
        const content = document.getElementById('content');
        const toggleSidebar = document.getElementById('toggleSidebar');
        const openSidebar = document.getElementById('openSidebar');

        toggleSidebar.addEventListener('click', () => {
            sidebar.classList.toggle('sidebar-hidden');
            sidebar.classList.toggle('sidebar-open');
            content.classList.toggle('content-shift');
            content.classList.toggle('content-full');
        });

        openSidebar.addEventListener('click', () => {
            sidebar.classList.remove('sidebar-hidden');
            sidebar.classList.add('sidebar-open');
            content.classList.remove('content-full');
            content.classList.add('content-shift');
        });

        function deleteReseller(resellerName) {
            if (confirm('Tem certeza que deseja excluir esta revenda?')) {
                fetch('/delete_reseller/' + resellerName, { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            location.reload();
                        } else {
                            alert(data.message);
                        }
                    });
            }
        }
    </script>
</body>
</html>
"""

client_info_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Informações do Cliente - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .sidebar { transition: width 0.3s ease; }
        .sidebar-hidden { width: 0; overflow: hidden; }
        .sidebar-open { width: 250px; }
        .content-shift { margin-left: 250px; transition: margin-left 0.3s ease; }
        .content-full { margin-left: 0; }
    </style>
</head>
<body class="bg-gray-100 font-sans">
    <div class="flex h-screen">
        <!-- Sidebar -->
        <div id="sidebar" class="sidebar sidebar-hidden {{ layout_settings.header_color }} text-white fixed h-full">
            <div class="p-4">
                <h2 class="text-xl font-bold">Painel IPTV</h2>
                <button id="toggleSidebar" class="mt-2 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                    </svg>
                </button>
            </div>
            <div class="p-4">
                <p class="text-sm">Bem-vindo, {{ username }}!</p>
                <p class="text-sm">Créditos: {{ credits }}</p>
            </div>
            <nav class="mt-4">
                <a href="{{ url_for('dashboard') }}" class="block py-2 px-4 hover:bg-blue-700">Dashboard</a>
                <a href="{{ url_for('profile') }}" class="block py-2 px-4 hover:bg-blue-700">Perfil</a>
                <a href="{{ url_for('ger_clientes') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Clientes</a>
                <a href="{{ url_for('ger_resellers') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Revendas</a>
                {% if role == 'admin' %}
                    <a href="{{ url_for('ferramenta') }}" class="block py-2 px-4 hover:bg-blue-700">Ferramentas</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="block py-2 px-4 hover:bg-blue-700">Sair</a>
            </nav>
        </div>
        <!-- Main Content -->
        <div id="content" class="flex-1 content-full p-6">
            <button id="openSidebar" class="mb-4 focus:outline-none">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                </svg>
            </button>
            <h1 class="text-2xl font-bold mb-6">Informações do Cliente</h1>
            <div class="bg-white p-6 rounded-lg shadow">
                <pre>{{ client_info }}</pre>
                <a href="{{ url_for('ger_clientes') }}" class="inline-block mt-4 p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Voltar</a>
            </div>
        </div>
    </div>
    <script>
        const sidebar = document.getElementById('sidebar');
        const content = document.getElementById('content');
        const toggleSidebar = document.getElementById('toggleSidebar');
        const openSidebar = document.getElementById('openSidebar');

        toggleSidebar.addEventListener('click', () => {
            sidebar.classList.toggle('sidebar-hidden');
            sidebar.classList.toggle('sidebar-open');
            content.classList.toggle('content-shift');
            content.classList.toggle('content-full');
        });

        openSidebar.addEventListener('click', () => {
            sidebar.classList.remove('sidebar-hidden');
            sidebar.classList.add('sidebar-open');
            content.classList.remove('content-full');
            content.classList.add('content-shift');
        });
    </script>
</body>
</html>
"""

ferramenta_html = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ferramentas - Painel IPTV</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .sidebar { transition: width 0.3s ease; }
        .sidebar-hidden { width: 0; overflow: hidden; }
        .sidebar-open { width: 250px; }
        .content-shift { margin-left: 250px; transition: margin-left 0.3s ease; }
        .content-full { margin-left: 0; }
    </style>
</head>
<body class="bg-gray-100 font-sans">
    <div class="flex h-screen">
        <!-- Sidebar -->
        <div id="sidebar" class="sidebar sidebar-hidden {{ layout_settings.header_color }} text-white fixed h-full">
            <div class="p-4">
                <h2 class="text-xl font-bold">Painel IPTV</h2>
                <button id="toggleSidebar" class="mt-2 focus:outline-none">
                    <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                    </svg>
                </button>
            </div>
            <div class="p-4">
                <p class="text-sm">Bem-vindo, {{ username }}!</p>
                <p class="text-sm">Créditos: {{ credits }}</p>
            </div>
            <nav class="mt-4">
                <a href="{{ url_for('dashboard') }}" class="block py-2 px-4 hover:bg-blue-700">Dashboard</a>
                <a href="{{ url_for('profile') }}" class="block py-2 px-4 hover:bg-blue-700">Perfil</a>
                <a href="{{ url_for('ger_clientes') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Clientes</a>
                <a href="{{ url_for('ger_resellers') }}" class="block py-2 px-4 hover:bg-blue-700">Ger. Revendas</a>
                {% if role == 'admin' %}
                    <a href="{{ url_for('ferramenta') }}" class="block py-2 px-4 hover:bg-blue-700">Ferramentas</a>
                {% endif %}
                <a href="{{ url_for('logout') }}" class="block py-2 px-4 hover:bg-blue-700">Sair</a>
            </nav>
        </div>
        <!-- Main Content -->
        <div id="content" class="flex-1 content-full p-6">
            <button id="openSidebar" class="mb-4 focus:outline-none">
                <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                </svg>
            </button>
            <h1 class="text-2xl font-bold mb-6">Ferramentas</h1>
            <div class="bg-white p-6 rounded-lg shadow mb-6">
                <h2 class="text-xl font-semibold mb-4">Manutenção</h2>
                <form method="POST" action="{{ url_for('toggle_maintenance') }}">
                    <button type="submit" class="p-2 bg-{{ 'red' if maintenance_mode else 'green' }}-500 text-white rounded hover:bg-{{ 'red' if maintenance_mode else 'green' }}-600">
                        {{ 'Desativar Manutenção' if maintenance_mode else 'Ativar Manutenção' }}
                    </button>
                </form>
            </div>
            <div class="bg-white p-6 rounded-lg shadow mb-6">
                <h2 class="text-xl font-semibold mb-4">Configurações de Layout</h2>
                <form method="POST" action="{{ url_for('update_layout') }}">
                    <div class="mb-4">
                        <label class="block text-gray-700">Cor do Cabeçalho</label>
                        <select name="header_color" class="w-full p-2 border rounded">
                            <option value="bg-blue-500" {% if layout_settings.header_color == 'bg-blue-500' %}selected{% endif %}>Azul</option>
                            <option value="bg-green-500" {% if layout_settings.header_color == 'bg-green-500' %}selected{% endif %}>Verde</option>
                            <option value="bg-red-500" {% if layout_settings.header_color == 'bg-red-500' %}selected{% endif %}>Vermelho</option>
                        </select>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">Texto de Boas-Vindas</label>
                        <input type="text" name="welcome_text" value="{{ layout_settings.welcome_text }}" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">URL da Imagem de Login</label>
                        <input type="text" name="login_image_url" value="{{ layout_settings.login_image_url }}" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <button type="submit" class="p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Salvar</button>
                </form>
            </div>
            <div class="bg-white p-6 rounded-lg shadow">
                <h2 class="text-xl font-semibold mb-4">Editar Template de Clientes</h2>
                <form method="POST" action="{{ url_for('update_client_template') }}">
                    <div class="mb-4">
                        <label class="block text-gray-700">Template de Informações do Cliente</label>
                        <textarea name="client_info_template" class="w-full p-2 border rounded" rows="5">{{ layout_settings.client_info_template }}</textarea>
                        <p class="text-sm text-gray-600">Use: #user_iptv#, #pass_iptv#, #url_m3u#, #dns_iptv#, #dns_iptv2#, #dns_iptv3#</p>
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">DNS 2 (#dns_iptv2#)</label>
                        <input type="text" name="public_url2" value="{{ layout_settings.public_url2 }}" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div class="mb-4">
                        <label class="block text-gray-700">DNS 3 (#dns_iptv3#)</label>
                        <input type="text" name="public_url3" value="{{ layout_settings.public_url3 }}" class="w-full p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500">
                    </div>
                    <button type="submit" class="p-2 bg-blue-500 text-white rounded hover:bg-blue-600">Salvar</button>
                </form>
            </div>
        </div>
    </div>
    <script>
        const sidebar = document.getElementById('sidebar');
        const content = document.getElementById('content');
        const toggleSidebar = document.getElementById('toggleSidebar');
        const openSidebar = document.getElementById('openSidebar');

        toggleSidebar.addEventListener('click', () => {
            sidebar.classList.toggle('sidebar-hidden');
            sidebar.classList.toggle('sidebar-open');
            content.classList.toggle('content-shift');
            content.classList.toggle('content-full');
        });

        openSidebar.addEventListener('click', () => {
            sidebar.classList.remove('sidebar-hidden');
            sidebar.classList.add('sidebar-open');
            content.classList.remove('content-full');
            content.classList.add('content-shift');
        });
    </script>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def login():
    if "username" in session and session.get("role") == "admin":
        return redirect(url_for("dashboard"))
    if MAINTENANCE_MODE and ("username" not in session or session.get("role") != "admin"):
        return render_template_string(login_html, maintenance_mode=True, layout_settings=layout_settings_db)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if not username or not password:
            flash("Preencha todos os campos!")
            return render_template_string(login_html, layout_settings=layout_settings_db)
        if username in users_db and users_db[username]["password"] == password:
            session["username"] = username
            session["role"] = users_db[username]["role"]
            save_db(users_db, USERS_DB_FILE)
            return redirect(url_for("dashboard"))
        elif username in resellers_db and resellers_db[username]["password"] == password:
            session["username"] = username
            session["role"] = "reseller"
            save_db(resellers_db, RESELLERS_DB_FILE)
            return redirect(url_for("dashboard"))
        else:
            flash("Credenciais inválidas!")
    return render_template_string(login_html, layout_settings=layout_settings_db)

@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    role = session.get("role", "reseller")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
        clients_count = len(clients_db)
        resellers_count = len(resellers_db)
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
        reseller_clients = resellers_db.get(session["username"], {}).get("clients", [])
        clients_count = len([cid for cid in reseller_clients if cid in clients_db])
        reseller_resellers = resellers_db.get(session["username"], {}).get("sub_resellers", [])
        resellers_count = len([rid for rid in reseller_resellers if rid in resellers_db])
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    welcome_text = layout_settings_db["welcome_text"].replace("{{ username }}", session["username"])
    layout_settings_db["welcome_text"] = welcome_text
    return render_template_string(dashboard_html, username=session["username"], role=role, clients_count=clients_count, resellers_count=resellers_count, credits=credits_display, layout_settings=layout_settings_db)

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    role = session.get("role", "reseller")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
    user_email = users_db.get(session["username"], {}).get("email", "") if role == "admin" else resellers_db.get(session["username"], {}).get("email", "")
    if request.method == "POST":
        new_email = request.form.get("email", "").strip()
        new_password = request.form.get("password", "").strip()
        if role == "admin":
            if new_email:
                users_db[session["username"]]["email"] = new_email
            if new_password:
                users_db[session["username"]]["password"] = new_password
            save_db(users_db, USERS_DB_FILE)
        else:
            if new_email:
                resellers_db[session["username"]]["email"] = new_email
            if new_password:
                resellers_db[session["username"]]["password"] = new_password
            save_db(resellers_db, RESELLERS_DB_FILE)
        flash("Perfil atualizado com sucesso!")
        return redirect(url_for("dashboard"))
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    return render_template_string(profile_html, credits=credits_display, role=role, user_email=user_email, layout_settings=layout_settings_db)

@app.route("/ger_clientes", methods=["GET", "POST"])
def ger_clientes():
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    role = session.get("role", "reseller")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
    if request.method == "POST":
        client_name = request.form.get("client_name", "").strip()
        client_password = request.form.get("client_password", "").strip()
        try:
            connections = int(request.form.get("connections", 1))
            months = int(request.form.get("months", 1))
        except ValueError:
            flash("Conexões e meses devem ser números válidos!")
            return redirect(url_for("ger_clientes"))
        if not client_name or not client_password:
            flash("Preencha todos os campos!")
            return redirect(url_for("ger_clientes"))
        total_credits_needed = connections + months + 1
        if role == "admin":
            user_credits = users_db.get(session["username"], {}).get("credits", float('inf'))
            has_infinite_credits = users_db.get(session["username"], {}).get("infinite_credits", True)
        else:
            user_credits = resellers_db.get(session["username"], {}).get("credits", 0)
            has_infinite_credits = resellers_db.get(session["username"], {}).get("infinite_credits", False)
        if not has_infinite_credits and user_credits < total_credits_needed:
            flash("Créditos insuficientes!")
            return redirect(url_for("ger_clientes"))
        client_id = str(uuid.uuid4())
        expiry_date = (datetime.now() + timedelta(days=30 * months)).strftime("%Y-%m-%d")
        clients_db[client_id] = {
            "name": client_name,
            "password": client_password,
            "connections": connections,
            "expiry_date": expiry_date,
            "status": "active" if datetime.strptime(expiry_date, "%Y-%m-%d") > datetime.now() else "expired",
            "owner": session["username"]
        }
        if role != "admin":
            resellers_db[session["username"]].setdefault("clients", []).append(client_id)
            save_db(resellers_db, RESELLERS_DB_FILE)
        if not has_infinite_credits:
            if role == "admin":
                users_db[session["username"]]["credits"] = max(0, user_credits - total_credits_needed)
                save_db(users_db, USERS_DB_FILE)
            else:
                resellers_db[session["username"]]["credits"] = max(0, user_credits - total_credits_needed)
                save_db(resellers_db, RESELLERS_DB_FILE)
        save_db(clients_db, CLIENTS_DB_FILE)
        flash(f"Cliente {client_name} criado! Créditos usados: {total_credits_needed if not has_infinite_credits else 0}")
        return redirect(url_for("ger_clientes"))
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    return render_template_string(ger_clientes_html, clients=clients_db, credits=credits_display, role=role, layout_settings=layout_settings_db, session=session)

@app.route("/ger_resellers", methods=["GET", "POST"])
def ger_resellers():
    if "username" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))
    if MAINTENANCE_MODE:
        return redirect(url_for("login"))
    credits = users_db.get(session["username"], {}).get("credits", float('inf'))
    if request.method == "POST":
        reseller_name = request.form.get("reseller_name", "").strip()
        reseller_password = request.form.get("reseller_password", "").strip()
        try:
            reseller_credits = int(request.form.get("credits", 0))
        except ValueError:
            flash("Créditos devem ser um número válido!")
            return redirect(url_for("ger_resellers"))
        if not reseller_name or not reseller_password:
            flash("Preencha todos os campos!")
            return redirect(url_for("ger_resellers"))
        if reseller_name in resellers_db:
            flash("Nome de revenda já existe!")
            return redirect(url_for("ger_resellers"))
        resellers_db[reseller_name] = {
            "password": reseller_password,
            "credits": reseller_credits,
            "infinite_credits": False,
            "email": "",
            "created_by": session["username"],
            "clients": [],
            "sub_resellers": []
        }
        resellers_db[session["username"]].setdefault("sub_resellers", []).append(reseller_name)
        save_db(resellers_db, RESELLERS_DB_FILE)
        flash(f"Revenda {reseller_name} criada com sucesso!")
        return redirect(url_for("ger_resellers"))
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    return render_template_string(ger_resellers_html, resellers=resellers_db, credits=credits_display, role=session.get("role", "reseller"), layout_settings=layout_settings_db, session=session)

@app.route("/client_info/<client_id>")
def client_info(client_id):
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    if client_id not in clients_db:
        flash("Cliente não encontrado!")
        return redirect(url_for("ger_clientes"))
    role = session.get("role", "reseller")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
    client = clients_db[client_id]
    access_url = f"{PUBLIC_URL}/get.php?username={client['name']}&password={client['password']}&type=m3u_plus&output=mpegts"
    client_info = layout_settings_db["client_info_template"]
    client_info = client_info.replace("#user_iptv#", client["name"])
    client_info = client_info.replace("#pass_iptv#", client["password"])
    client_info = client_info.replace("#url_m3u#", access_url)
    client_info = client_info.replace("#dns_iptv#", PUBLIC_URL)
    client_info = client_info.replace("#dns_iptv2#", layout_settings_db["public_url2"])
    client_info = client_info.replace("#dns_iptv3#", layout_settings_db["public_url3"])
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    return render_template_string(client_info_html, client_info=client_info, credits=credits_display, role=role, layout_settings=layout_settings_db, username=session["username"])

@app.route("/ferramenta")
def ferramenta():
    if "username" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))
    credits = users_db.get(session["username"], {}).get("credits", float('inf'))
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    return render_template_string(ferramenta_html, credits=credits_display, maintenance_mode=MAINTENANCE_MODE, layout_settings=layout_settings_db, username=session["username"], role="admin")

@app.route("/toggle_maintenance", methods=["POST"])
def toggle_maintenance():
    if "username" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))
    global MAINTENANCE_MODE
    MAINTENANCE_MODE = not MAINTENANCE_MODE
    flash(f"Modo de manutenção {'ativado' if MAINTENANCE_MODE else 'desativado'}!")
    return redirect(url_for("ferramenta"))

@app.route("/update_layout", methods=["POST"])
def update_layout():
    if "username" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))
    layout_settings_db["header_color"] = request.form.get("header_color", "bg-blue-500")
    layout_settings_db["welcome_text"] = request.form.get("welcome_text", "Bem-vindo, {{ username }}!")
    layout_settings_db["login_image_url"] = request.form.get("login_image_url", "")
    save_db(layout_settings_db, LAYOUT_SETTINGS_FILE)
    flash("Configurações de layout atualizadas!")
    return redirect(url_for("ferramenta"))

@app.route("/update_client_template", methods=["POST"])
def update_client_template():
    if "username" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))
    layout_settings_db["client_info_template"] = request.form.get("client_info_template", "Usuário: #user_iptv#\nSenha: #pass_iptv#\nM3U: #url_m3u#\nDNS1: #dns_iptv#\nDNS2: #dns_iptv2#\nDNS3: #dns_iptv3#")
    layout_settings_db["public_url2"] = request.form.get("public_url2", PUBLIC_URL2)
    layout_settings_db["public_url3"] = request.form.get("public_url3", PUBLIC_URL3)
    save_db(layout_settings_db, LAYOUT_SETTINGS_FILE)
    flash("Template de clientes atualizado!")
    return redirect(url_for("ferramenta"))

@app.route("/logout")
def logout():
    session.pop("username", None)
    session.pop("role", None)
    return redirect(url_for("login"))

@app.route("/toggle_block/<client_id>", methods=["POST"])
def toggle_block(client_id):
    if "username" not in session or session.get("role") not in ["admin", "reseller"]:
        return jsonify({"success": False, "message": "Acesso negado!"})
    if client_id not in clients_db:
        return jsonify({"success": False, "message": "Cliente não encontrado!"})
    if session.get("role") == "reseller" and clients_db[client_id]["owner"] != session["username"]:
        return jsonify({"success": False, "message": "Acesso negado! Este cliente não pertence a você!"})
    clients_db[client_id]["status"] = "blocked" if clients_db[client_id]["status"] == "active" else "active"
    save_db(clients_db, CLIENTS_DB_FILE)
    return jsonify({"success": True})

@app.route("/delete_client/<client_id>", methods=["POST"])
def delete_client(client_id):
    if "username" not in session or session.get("role") not in ["admin", "reseller"]:
        return jsonify({"success": False, "message": "Acesso negado!"})
    if client_id not in clients_db:
        return jsonify({"success": False, "message": "Cliente não encontrado!"})
    if session.get("role") == "reseller" and clients_db[client_id]["owner"] != session["username"]:
        return jsonify({"success": False, "message": "Acesso negado! Este cliente não pertence a você!"})
    if session.get("role") == "reseller":
        resellers_db[session["username"]]["clients"].remove(client_id)
        save_db(resellers_db, RESELLERS_DB_FILE)
    del clients_db[client_id]
    save_db(clients_db, CLIENTS_DB_FILE)
    return jsonify({"success": True})

@app.route("/delete_reseller/<reseller_name>", methods=["POST"])
def delete_reseller(reseller_name):
    if "username" not in session or session.get("role") != "admin":
        return jsonify({"success": False, "message": "Acesso negado!"})
    if reseller_name not in resellers_db:
        return jsonify({"success": False, "message": "Revenda não encontrada!"})
    for client_id in resellers_db[reseller_name].get("clients", []):
        if client_id in clients_db:
            del clients_db[client_id]
    for sub_reseller in resellers_db[reseller_name].get("sub_resellers", []):
        if sub_reseller in resellers_db:
            del resellers_db[sub_reseller]
    if resellers_db[reseller_name]["created_by"] in resellers_db:
        resellers_db[resellers_db[reseller_name]["created_by"]]["sub_resellers"].remove(reseller_name)
    del resellers_db[reseller_name]
    save_db(resellers_db, RESELLERS_DB_FILE)
    save_db(clients_db, CLIENTS_DB_FILE)
    return jsonify({"success": True})

@app.route("/get.php")
def get_m3u():
    username = request.args.get("username")
    password = request.args.get("password")
    output = request.args.get("output", "mpegts")
    if not username or not password:
        return "Parâmetros inválidos!", 400
    client_id = next((cid for cid, client in clients_db.items() if client["name"] == username and client["password"] == password and client["status"] == "active"), None)
    if not client_id:
        return "Credenciais inválidas ou cliente bloqueado!", 403
    if datetime.now() > datetime.strptime(clients_db[client_id]["expiry_date"], "%Y-%m-%d"):
        clients_db[client_id]["status"] = "expired"
        save_db(clients_db, CLIENTS_DB_FILE)
        return "Acesso expirado!", 403
    global channels_cache
    if not channels_cache["data"] or (channels_cache["last_updated"] and (datetime.now() - channels_cache["last_updated"]) > CACHE_TIMEOUT):
        channels_cache["data"] = fetch_m3u(M3U_URL)
        channels_cache["last_updated"] = datetime.now()
    m3u_content = "#EXTM3U\n"
    grouped_channels = {}
    for channel in channels_cache["data"]:
        group = channel.get("group", "Outros")
        grouped_channels.setdefault(group, []).append(channel)
    for group, channels in grouped_channels.items():
        for channel in channels:
            m3u_content += f'#EXTINF:-1 tvg-id="{channel["tvg_id"]}" tvg-name="{channel["name"]}" tvg-logo="{channel["logo"]}" group-title="{group}",{channel["title"]}\n'
            m3u_content += f"{channel['url']}\n"
    print(f"Generated M3U with {len(channels_cache['data'])} channels for user {username}")
    response = Response(m3u_content, mimetype="application/x-mpegURL")
    response.headers["Content-Disposition"] = "attachment; filename=playlist.m3u"
    return response

@app.route("/player_api.php")
def player_api():
    username = request.args.get("username")
    password = request.args.get("password")
    action = request.args.get("action")
    client_id = next((cid for cid, client in clients_db.items() if client["name"] == username and client["password"] == password), None)
    if not client_id:
        return jsonify({"message": "Credenciais inválidas", "status": "error"}), 401
    client = clients_db[client_id]
    if action == "user_info":
        return jsonify({
            "username": username,
            "password": password,
            "status": client["status"],
            "exp_date": client["expiry_date"],
            "max_connections": client["connections"],
            "active_connections": 0
        })
    return jsonify({"message": "Ação não suportada", "status": "error"}), 400

@app.route("/xmltv.php")
def xmltv():
    username = request.args.get("username")
    password = request.args.get("password")
    client_id = next((cid for cid, client in clients_db.items() if client["name"] == username and client["password"] == password), None)
    if not client_id:
        return "Credenciais inválidas", 401
    xmltv_content = '\n\n\n'
    return Response(xmltv_content, mimetype="application/xml", headers={"Content-Disposition": "attachment; filename=epg.xml"})

if __name__ == "__main__":
    channels_cache["data"] = fetch_m3u(M3U_URL)
    channels_cache["last_updated"] = datetime.now()
    print(f"Total de canais encontrados: {len(channels_cache['data'])}")
    app.run(debug=True, host="0.0.0.0", port=8080)