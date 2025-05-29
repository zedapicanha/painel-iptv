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
PUBLIC_URL = "https://ultraservertop.serveo.net"
PUBLIC_URL2 = "http://backup1.jontexplay.serveo.net"
PUBLIC_URL3 = "http://backup2.jontexplay.serveo.net"

# Source M3U URL
M3U_URL = "http://facilita.fun/get.php?username=492653&password=891525&type=m3u_plus&output=hls"

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
                        reseller.setdefault("infinite_credits", reseller.get("role") == "franchise")
                        reseller.setdefault("email", "")
                        reseller.setdefault("clients", [])
                        reseller.setdefault("sub_resellers", [])
                        reseller.setdefault("role", "simple")
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

def sanitize_credentials(text: str) -> str:
    """Remove caracteres especiais, permitindo apenas alfanuméricos, _ e -."""
    return re.sub(r'[^a-zA-Z0-9_-]', '', text)

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
<html>
<head>
    <title>Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded-lg shadow-lg w-full max-w-md">
        {% if maintenance_mode and session.get('role') != 'admin' %}
            <div class="text-center">
                <h1 class="text-2xl font-bold text-red-600 mb-4">Atenção ⚠️ Servidor em manutenção!</h1>
            </div>
        {% else %}
            {% if layout_settings.login_image_url %}
                <div class="flex justify-{{ layout_settings.login_image_position }} mb-4">
                    <img src="{{ layout_settings.login_image_url }}" width="{{ layout_settings.login_image_width }}" height="{{ layout_settings.login_image_height }}" alt="Login Image">
                </div>
            {% endif %}
            <h2 class="text-2xl font-bold mb-6 text-center">Login</h2>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <div class="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded mb-4">
                        {{ messages[0] }}
                    </div>
                {% endif %}
            {% endwith %}
            <form method="POST" action="{{ url_for('login') }}">
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="username">Usuário</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="username" name="username" type="text" placeholder="Usuário">
                </div>
                <div class="mb-6">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="password">Senha</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 mb-3 leading-tight focus:outline-none focus:shadow-outline" id="password" name="password" type="password" placeholder="Senha">
                </div>
                <div class="flex items-center justify-between">
                    <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline" type="submit">Entrar</button>
                </div>
            </form>
        {% endif %}
        <div class="mt-8 text-center text-sm text-gray-600">
            By SvO'Panel / <a href="https://t.me/svopanel" class="text-blue-500 hover:text-blue-700">Telegram (Acessar)</a>
        </div>
    </div>
</body>
</html>
"""

sidebar_html = """
<aside class="fixed inset-y-0 left-0 w-64 bg-gray-800 text-white flex flex-col z-50">
    <div class="p-4 flex items-center">
        <img src="{{ layout_settings.login_image_url }}" class="h-10 w-10 rounded-full mr-2" alt="User Image">
        <div>
            <p class="font-bold">{{ username }}</p>
            <p class="text-sm">Créditos: {{ credits }}</p>
        </div>
    </div>
    <nav class="flex-1 px-2 py-4">
        <ul class="space-y-2">
            <li><a href="{{ url_for('dashboard') }}" class="block px-4 py-2 hover:bg-gray-700 rounded">Dashboard</a></li>
            <li><a href="{{ url_for('profile') }}" class="block px-4 py-2 hover:bg-gray-700 rounded">Perfil</a></li>
            <li><a href="{{ url_for('ger_clientes') }}" class="block px-4 py-2 hover:bg-gray-700 rounded">Ger. Clientes</a></li>
            {% if role == 'admin' or role == 'master' or role == 'franchise' %}
            <li><a href="{{ url_for('ger_resellers') }}" class="block px-4 py-2 hover:bg-gray-700 rounded">Ger. Revendas</a></li>
            {% endif %}
            {% if role == 'admin' %}
            <li><a href="{{ url_for('ferramenta') }}" class="block px-4 py-2 hover:bg-gray-700 rounded">Ferramentas</a></li>
            {% endif %}
            <li><a href="{{ url_for('logout') }}" class="block px-4 py-2 hover:bg-gray-700 rounded">Sair</a></li>
        </ul>
    </nav>
    <div class="p-4 text-sm text-gray-400">
        Versão 1.0.8
    </div>
</aside>
"""

dashboard_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100">
    {{ sidebar|safe }}
    <div class="ml-64 p-6">
        <h2 class="text-2xl font-bold mb-6">{{ layout_settings.welcome_text }}</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="bg-white p-6 rounded-lg shadow">
                <h3 class="text-xl font-bold mb-2">Clientes</h3>
                <p class="text-2xl">{{ clients_count }}</p>
            </div>
            {% if role == 'admin' or role == 'master' or role == 'franchise' %}
            <div class="bg-white p-6 rounded-lg shadow">
                <h3 class="text-xl font-bold mb-2">Revendas</h3>
                <p class="text-2xl">{{ resellers_count }}</p>
            </div>
            {% endif %}
        </div>
    </div>
</body>
</html>
"""

profile_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Perfil</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100">
    {{ sidebar|safe }}
    <div class="ml-64 p-6">
        <h2 class="text-2xl font-bold mb-6">Perfil</h2>
        {% with messages = get_flashed_messages() %}
            {% if messages %}
                <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
                    {{ messages[0] }}
                </div>
            {% endif %}
        {% endwith %}
        <form method="POST" action="{{ url_for('profile') }}">
            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="email">Email</label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="email" name="email" type="email" value="{{ user_email }}" placeholder="Email">
            </div>
            <div class="mb-6">
                <label class="block text-gray-700 text-sm font-bold mb-2" for="password">Nova Senha</label>
                <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="password" name="password" type="password" placeholder="Nova Senha">
            </div>
            <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline" type="submit">Salvar</button>
        </form>
    </div>
</body>
</html>
"""

ger_clientes_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Gerenciar Clientes</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        function adjustValue(id, delta) {
            let input = document.getElementById(id);
            let value = parseInt(input.value) || 0;
            value = Math.max(1, value + delta);
            input.value = value;
        }
        async function toggleBlock(clientId) {
            const response = await fetch(`/toggle_block/${clientId}`, { method: 'POST' });
            const result = await response.json();
            if (result.success) {
                location.reload();
            } else {
                alert(result.message);
            }
        }
        async function deleteClient(clientId) {
            if (confirm('Tem certeza que deseja excluir este cliente?')) {
                const response = await fetch(`/delete_client/${clientId}`, { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    location.reload();
                } else {
                    alert(result.message);
                }
            }
        }
    </script>
</head>
<body class="bg-gray-100">
    {{ sidebar|safe }}
    <div class="ml-64 p-6">
        <h2 class="text-2xl font-bold mb-6">Gerenciar Clientes</h2>
        <div class="bg-white p-6 rounded-lg shadow mb-6">
            <h3 class="text-xl font-bold mb-4">Criar Cliente</h3>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
                        {{ messages[0] }}
                    </div>
                {% endif %}
            {% endwith %}
            <form method="POST" action="{{ url_for('ger_clientes') }}">
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="client_name">Nome do Cliente</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="client_name" name="client_name" type="text" placeholder="Nome do Cliente">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="client_password">Senha</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="client_password" name="client_password" type="password" placeholder="Senha">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="connections">Conexões</label>
                    <div class="flex items-center">
                        <button type="button" onclick="adjustValue('connections', -1)" class="bg-gray-300 hover:bg-gray-400 text-gray-800 font-bold py-1 px-2 rounded-l">-</button>
                        <input class="shadow appearance-none border rounded w-20 py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline text-center" id="connections" name="connections" type="number" value="1" min="1">
                        <button type="button" onclick="adjustValue('connections', 1)" class="bg-gray-300 hover:bg-gray-400 text-gray-800 font-bold py-1 px-2 rounded-r">+</button>
                    </div>
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="months">Meses de Validade</label>
                    <div class="flex items-center">
                        <button type="button" onclick="adjustValue('months', -1)" class="bg-gray-300 hover:bg-gray-400 text-gray-800 font-bold py-1 px-2 rounded-l">-</button>
                        <input class="shadow appearance-none border rounded w-20 py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline text-center" id="months" name="months" type="number" value="1" min="1">
                        <button type="button" onclick="adjustValue('months', 1)" class="bg-gray-300 hover:bg-gray-400 text-gray-800 font-bold py-1 px-2 rounded-r">+</button>
                    </div>
                </div>
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline" type="submit">Criar</button>
            </form>
        </div>
        <div class="bg-white p-6 rounded-lg shadow">
            <h3 class="text-xl font-bold mb-4">Lista de Clientes</h3>
            <div class="overflow-x-auto">
                <table class="min-w-full bg-white">
                    <thead>
                        <tr>
                            <th class="py-2 px-4 border-b">Nome</th>
                            <th class="py-2 px-4 border-b">Senha</th>
                            <th class="py-2 px-4 border-b">Conexões</th>
                            <th class="py-2 px-4 border-b">Vencimento</th>
                            <th class="py-2 px-4 border-b">Status</th>
                            <th class="py-2 px-4 border-b">Ações</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for client_id, client in clients.items() %}
                        {% if role == 'admin' or client.owner == session['username'] %}
                        <tr>
                            <td class="py-2 px-4 border-b">{{ client.name }}</td>
                            <td class="py-2 px-4 border-b">{{ client.password }}</td>
                            <td class="py-2 px-4 border-b">{{ client.connections }}</td>
                            <td class="py-2 px-4 border-b">{{ client.expiry_date }}</td>
                            <td class="py-2 px-4 border-b">{{ client.status }}</td>
                            <td class="py-2 px-4 border-b">
                                <a href="{{ url_for('client_info', client_id=client_id) }}" class="text-blue-500 hover:text-blue-700 mr-2">Ver Infos</a>
                                <button onclick="toggleBlock('{{ client_id }}')" class="text-{{ 'red' if client.status == 'active' else 'green' }}-500 hover:text-{{ 'red' if client.status == 'active' else 'green' }}-700 mr-2">{{ 'Bloquear' if client.status == 'active' else 'Desbloquear' }}</button>
                                <button onclick="deleteClient('{{ client_id }}')" class="text-red-500 hover:text-red-700">Excluir</button>
                            </td>
                        </tr>
                        {% endif %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>
"""

ger_resellers_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Gerenciar Revendas</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        async function deleteReseller(resellerName) {
            if (confirm('Tem certeza que deseja excluir esta revenda?')) {
                const response = await fetch(`/delete_reseller/${resellerName}`, { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    location.reload();
                } else {
                    alert(result.message);
                }
            }
        }
    </script>
</head>
<body class="bg-gray-100">
    {{ sidebar|safe }}
    <div class="ml-64 p-6">
        <h2 class="text-2xl font-bold mb-6">Gerenciar Revendas</h2>
        <div class="bg-white p-6 rounded-lg shadow mb-6">
            <h3 class="text-xl font-bold mb-4">Criar Revenda</h3>
            {% with messages = get_flashed_messages() %}
                {% if messages %}
                    <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
                        {{ messages[0] }}
                    </div>
                {% endif %}
            {% endwith %}
            <form method="POST" action="{{ url_for('ger_resellers') }}">
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="reseller_name">Nome da Revenda</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="reseller_name" name="reseller_name" type="text" placeholder="Nome da Revenda">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="reseller_password">Senha</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="reseller_password" name="reseller_password" type="password" placeholder="Senha">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="credits">Créditos</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="credits" name="credits" type="number" value="0" min="0" placeholder="Créditos">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="reseller_role">Selecione o cargo desse revendedor</label>
                    <select class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="reseller_role" name="reseller_role">
                        <option value="simple">Revendedor Simples</option>
                        <option value="master">Revenda Master</option>
                        {% if role == 'admin' %}
                        <option value="franchise">Franquia</option>
                        <option value="admin">Administrador</option>
                        {% endif %}
                    </select>
                </div>
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline" type="submit">Criar</button>
            </form>
        </div>
        <div class="bg-white p-6 rounded-lg shadow">
            <h3 class="text-xl font-bold mb-4">Lista de Revendas</h3>
            <div class="overflow-x-auto">
                <table class="min-w-full bg-white">
                    <thead>
                        <tr>
                            <th class="py-2 px-4 border-b">Nome</th>
                            <th class="py-2 px-4 border-b">Cargo</th>
                            <th class="py-2 px-4 border-b">Créditos</th>
                            <th class="py-2 px-4 border-b">Ações</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for reseller_name, reseller in resellers.items() %}
                        {% if role == 'admin' or reseller.created_by == session['username'] %}
                        <tr>
                            <td class="py-2 px-4 border-b">{{ reseller_name }}</td>
                            <td class="py-2 px-4 border-b">{{ reseller.role }}</td>
                            <td class="py-2 px-4 border-b">{{ 'Infinitos' if reseller.infinite_credits else reseller.credits }}</td>
                            <td class="py-2 px-4 border-b">
                                <button onclick="deleteReseller('{{ reseller_name }}')" class="text-red-500 hover:text-red-700">Excluir</button>
                            </td>
                        </tr>
                        {% endif %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</body>
</html>
"""

client_info_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Informações do Cliente</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        function copyClientInfo() {
            const clientInfo = document.getElementById('client-info').innerText;
            navigator.clipboard.writeText(clientInfo).then(() => {
                const button = document.getElementById('copy-button');
                button.innerText = 'Copiado!';
                button.classList.remove('bg-blue-500', 'hover:bg-blue-700');
                button.classList.add('bg-green-500');
                setTimeout(() => {
                    button.innerText = 'Copiar';
                    button.classList.remove('bg-green-500');
                    button.classList.add('bg-blue-500', 'hover:bg-blue-700');
                }, 2000);
            }).catch(err => {
                alert('Erro ao copiar: ' + err);
            });
        }
    </script>
</head>
<body class="bg-gray-100">
    {{ sidebar|safe }}
    <div class="ml-64 p-6">
        <h2 class="text-2xl font-bold mb-6">Informações do Cliente</h2>
        <div class="bg-white p-6 rounded-lg shadow">
            <pre id="client-info" class="whitespace-pre-wrap">{{ client_info }}</pre>
            <button id="copy-button" onclick="copyClientInfo()" class="mt-4 inline-block bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">Copiar</button>
            <a href="{{ url_for('ger_clientes') }}" class="mt-4 inline-block bg-gray-500 hover:bg-gray-700 text-white font-bold py-2 px-4 rounded ml-2">Voltar</a>
        </div>
    </div>
</body>
</html>
"""

ferramenta_html = """
<!DOCTYPE html>
<html>
<head>
    <title>Ferramentas</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100">
    {{ sidebar|safe }}
    <div class="ml-64 p-6">
        <h2 class="text-2xl font-bold mb-6">Ferramentas</h2>
        <div class="bg-white p-6 rounded-lg shadow mb-6">
            <h3 class="text-xl font-bold mb-4">Manutenção</h3>
            <form method="POST" action="{{ url_for('toggle_maintenance') }}">
                <button class="bg-{{ 'red' if maintenance_mode else 'green' }}-500 hover:bg-{{ 'red' if maintenance_mode else 'green' }}-700 text-white font-bold py-2 px-4 rounded" type="submit">{{ 'Desativar Manutenção' if maintenance_mode else 'Ativar Manutenção' }}</button>
            </form>
        </div>
        <div class="bg-white p-6 rounded-lg shadow mb-6">
            <h3 class="text-xl font-bold mb-4">Configurações de Layout</h3>
            <form method="POST" action="{{ url_for('update_layout') }}">
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="header_color">Cor do Cabeçalho</label>
                    <select class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="header_color" name="header_color">
                        <option value="bg-blue-500" {% if layout_settings.header_color == 'bg-blue-500' %}selected{% endif %}>Azul</option>
                        <option value="bg-green-500" {% if layout_settings.header_color == 'bg-green-500' %}selected{% endif %}>Verde</option>
                        <option value="bg-red-500" {% if layout_settings.header_color == 'bg-red-500' %}selected{% endif %}>Vermelho</option>
                    </select>
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="welcome_text">Texto de Boas-Vindas</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="welcome_text" name="welcome_text" type="text" value="{{ layout_settings.welcome_text }}" placeholder="Texto de Boas-Vindas">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="login_image_url">URL da Imagem de Login</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="login_image_url" name="login_image_url" type="text" value="{{ layout_settings.login_image_url }}" placeholder="URL da Imagem">
                </div>
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline" type="submit">Salvar</button>
            </form>
        </div>
        <div class="bg-white p-6 rounded-lg shadow">
            <h3 class="text-xl font-bold mb-4">Editar Template de Clientes</h3>
            <form method="POST" action="{{ url_for('update_client_template') }}">
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="client_info_template">Template de Informações do Cliente</label>
                    <textarea class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="client_info_template" name="client_info_template" rows="6">{{ layout_settings.client_info_template }}</textarea>
                    <p class="text-sm text-gray-600 mt-2">Use: #user_iptv#, #pass_iptv#, #url_m3u#, #dns_iptv#, #dns_iptv2#, #dns_iptv3#</p>
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="public_url2">DNS 2 (#dns_iptv2#)</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="public_url2" name="public_url2" type="text" value="{{ layout_settings.public_url2 }}" placeholder="DNS 2">
                </div>
                <div class="mb-4">
                    <label class="block text-gray-700 text-sm font-bold mb-2" for="public_url3">DNS 3 (#dns_iptv3#)</label>
                    <input class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:shadow-outline" id="public_url3" name="public_url3" type="text" value="{{ layout_settings.public_url3 }}" placeholder="DNS 3">
                </div>
                <button class="bg-blue-500 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none focus:shadow-outline" type="submit">Salvar</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def login():
    if "username" in session and session.get("role") in ["admin", "simple", "master", "franchise"]:
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
            session["role"] = resellers_db[username]["role"]
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
    role = session.get("role", "simple")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
        clients_count = len(clients_db)
        resellers_count = len(resellers_db)
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
        reseller_clients = [cid for cid, client in clients_db.items() if client["owner"] == session["username"]]
        clients_count = len(reseller_clients)
        if role in ["master", "franchise"]:
            reseller_resellers = [rid for rid, reseller in resellers_db.items() if reseller["created_by"] == session["username"]]
            resellers_count = len(reseller_resellers)
        else:
            resellers_count = 0
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    welcome_text = layout_settings_db["welcome_text"].replace("{{ username }}", session["username"])
    layout_settings_db["welcome_text"] = welcome_text
    sidebar = render_template_string(sidebar_html, 
                                   username=session["username"], 
                                   credits=credits_display, 
                                   role=role, 
                                   layout_settings=layout_settings_db)
    return render_template_string(dashboard_html, 
                                sidebar=sidebar,
                                username=session["username"], 
                                role=role, 
                                clients_count=clients_count, 
                                resellers_count=resellers_count, 
                                credits=credits_display, 
                                layout_settings=layout_settings_db)

@app.route("/profile", methods=["GET", "POST"])
def profile():
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    role = session.get("role", "simple")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
        user_db = users_db
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
        user_db = resellers_db
    user_email = user_db.get(session["username"], {}).get("email", "")
    
    if request.method == "POST":
        new_email = request.form.get("email", "").strip()
        new_password = request.form.get("password", "").strip()
        if new_email:
            user_db[session["username"]]["email"] = new_email
        if new_password:
            user_db[session["username"]]["password"] = sanitize_credentials(new_password)
        save_db(user_db, USERS_DB_FILE if role == "admin" else RESELLERS_DB_FILE)
        flash("Perfil atualizado com sucesso!")
        return redirect(url_for("dashboard"))
    
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    sidebar = render_template_string(sidebar_html, 
                                   username=session["username"], 
                                   credits=credits_display, 
                                   role=role, 
                                   layout_settings=layout_settings_db)
    return render_template_string(profile_html, 
                                sidebar=sidebar,
                                credits=credits_display, 
                                role=role, 
                                user_email=user_email, 
                                layout_settings=layout_settings_db,
                                username=session["username"])

@app.route("/ger_clientes", methods=["GET", "POST"])
def ger_clientes():
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    role = session.get("role", "simple")
    
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
        has_infinite_credits = users_db.get(session["username"], {}).get("infinite_credits", True)
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
        has_infinite_credits = resellers_db.get(session["username"], {}).get("infinite_credits", False)
    
    if request.method == "POST":
        client_name = request.form.get("client_name", "").strip()
        client_password = request.form.get("client_password", "").strip()
        try:
            connections = int(request.form.get("connections", 1))
            months = int(request.form.get("months", 1))
        except ValueError:
            flash("Conexões e meses devem ser números válidos!")
            return redirect(url_for("ger_clientes"))
        
        # Sanitize credentials
        client_name = sanitize_credentials(client_name)
        client_password = sanitize_credentials(client_password)
        
        if not client_name or not client_password:
            flash("Preencha todos os campos! Use apenas letras, números, _ ou -.")
            return redirect(url_for("ger_clientes"))
        
        # Check for duplicate client name
        if any(client["name"] == client_name for client in clients_db.values()):
            flash("Nome do cliente já existe!")
            return redirect(url_for("ger_clientes"))
        
        total_credits_needed = connections + months + 1
        if not has_infinite_credits and credits < total_credits_needed:
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
            resellers_db[session["username"]]["clients"].append(client_id)
            if not has_infinite_credits:
                resellers_db[session["username"]]["credits"] = max(0, credits - total_credits_needed)
            save_db(resellers_db, RESELLERS_DB_FILE)
        else:
            if not has_infinite_credits:
                users_db[session["username"]]["credits"] = max(0, credits - total_credits_needed)
                save_db(users_db, USERS_DB_FILE)
        
        save_db(clients_db, CLIENTS_DB_FILE)
        flash(f"Cliente {client_name} criado! Créditos usados: {total_credits_needed if not has_infinite_credits else 0}")
        return redirect(url_for("ger_clientes"))
    
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    sidebar = render_template_string(sidebar_html, 
                                   username=session["username"], 
                                   credits=credits_display, 
                                   role=role, 
                                   layout_settings=layout_settings_db)
    return render_template_string(ger_clientes_html, 
                                sidebar=sidebar,
                                clients=clients_db, 
                                credits=credits_display, 
                                role=role, 
                                layout_settings=layout_settings_db, 
                                session=session,
                                resellers_db=resellers_db,
                                username=session["username"])

@app.route("/ger_resellers", methods=["GET", "POST"])
def ger_resellers():
    if "username" not in session or session.get("role") not in ["admin", "master", "franchise"]:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    
    role = session.get("role")
    if role == "admin":
        credits = users_db.get(session["username"], {}).get("credits", float('inf'))
        has_infinite_credits = users_db.get(session["username"], {}).get("infinite_credits", True)
    else:
        credits = resellers_db.get(session["username"], {}).get("credits", 0)
        has_infinite_credits = resellers_db.get(session["username"], {}).get("infinite_credits", False)
    
    if request.method == "POST":
        reseller_name = request.form.get("reseller_name", "").strip()
        reseller_password = request.form.get("reseller_password", "").strip()
        try:
            reseller_credits = int(request.form.get("credits", 0))
        except ValueError:
            flash("Créditos devem ser um número válido!")
            return redirect(url_for("ger_resellers"))
        
        # Sanitize credentials
        reseller_name = sanitize_credentials(reseller_name)
        reseller_password = sanitize_credentials(reseller_password)
        
        reseller_role = request.form.get("reseller_role", "simple")
        if role != "admin" and reseller_role not in ["simple", "master"]:
            flash("Você não tem permissão para criar esse tipo de revenda!")
            return redirect(url_for("ger_resellers"))
        if not reseller_name or not reseller_password:
            flash("Preencha todos os campos! Use apenas letras, números, _ ou -.")
            return redirect(url_for("ger_resellers"))
        if reseller_name in resellers_db or reseller_name in users_db:
            flash("Nome de revenda já existe!")
            return redirect(url_for("ger_resellers"))
        
        if role != "admin" and not has_infinite_credits and credits < reseller_credits + 1:
            flash("Créditos insuficientes para criar revenda!")
            return redirect(url_for("ger_resellers"))
        
        if reseller_role == "admin":
            users_db[reseller_name] = {
                "password": reseller_password,
                "role": "admin",
                "credits": float('inf'),
                "infinite_credits": True,
                "email": "",
                "clients": [],
                "sub_resellers": []
            }
            save_db(users_db, USERS_DB_FILE)
        else:
            resellers_db[reseller_name] = {
                "password": reseller_password,
                "credits": reseller_credits,
                "infinite_credits": reseller_role == "franchise",
                "email": "",
                "created_by": session["username"],
                "clients": [],
                "sub_resellers": [],
                "role": reseller_role
            }
            # Ensure the creator has a sub_resellers list
            if session["username"] not in resellers_db:
                resellers_db[session["username"]] = {
                    "password": users_db.get(session["username"], {}).get("password", reseller_password),
                    "credits": credits,
                    "infinite_credits": has_infinite_credits,
                    "email": "",
                    "created_by": "system",
                    "clients": [],
                    "sub_resellers": [],
                    "role": role
                }
            resellers_db[session["username"]]["sub_resellers"].append(reseller_name)
            if role != "admin" and not has_infinite_credits:
                resellers_db[session["username"]]["credits"] = max(0, credits - (reseller_credits + 1))
            save_db(resellers_db, RESELLERS_DB_FILE)
        
        flash(f"Revenda {reseller_name} criada com sucesso!")
        return redirect(url_for("ger_resellers"))
    
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    sidebar = render_template_string(sidebar_html, 
                                   username=session["username"], 
                                   credits=credits_display, 
                                   role=role, 
                                   layout_settings=layout_settings_db)
    return render_template_string(ger_resellers_html, 
                                sidebar=sidebar,
                                resellers=resellers_db, 
                                credits=credits_display, 
                                role=role, 
                                layout_settings=layout_settings_db, 
                                session=session,
                                username=session["username"])

@app.route("/client_info/<client_id>")
def client_info(client_id):
    if "username" not in session:
        return redirect(url_for("login"))
    if MAINTENANCE_MODE and session.get("role") != "admin":
        return redirect(url_for("login"))
    if client_id not in clients_db:
        flash("Cliente não encontrado!")
        return redirect(url_for("ger_clientes"))
    
    role = session.get("role", "simple")
    if role != "admin" and clients_db[client_id]["owner"] != session["username"]:
        flash("Acesso negado!")
        return redirect(url_for("ger_clientes"))
    
    credits = users_db.get(session["username"], {}).get("credits", float('inf')) if role == "admin" else resellers_db.get(session["username"], {}).get("credits", 0)
    
    client = clients_db[client_id]
    access_url = f"{PUBLIC_URL}/get.php?username={client['name']}&password={client['password']}&type=m3u_plus&output=ts"
    client_info = layout_settings_db["client_info_template"]
    client_info = client_info.replace("#user_iptv#", client["name"])
    client_info = client_info.replace("#pass_iptv#", client["password"])
    client_info = client_info.replace("#url_m3u#", access_url)
    client_info = client_info.replace("#dns_iptv#", PUBLIC_URL)
    client_info = client_info.replace("#dns_iptv2#", layout_settings_db["public_url2"])
    client_info = client_info.replace("#dns_iptv3#", layout_settings_db["public_url3"])
    
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    sidebar = render_template_string(sidebar_html, 
                                   username=session["username"], 
                                   credits=credits_display, 
                                   role=role, 
                                   layout_settings=layout_settings_db)
    return render_template_string(client_info_html, 
                                sidebar=sidebar,
                                client_info=client_info, 
                                credits=credits_display, 
                                role=role, 
                                layout_settings=layout_settings_db, 
                                username=session["username"])

@app.route("/ferramenta")
def ferramenta():
    if "username" not in session or session.get("role") != "admin":
        return redirect(url_for("login"))
    credits = users_db.get(session["username"], {}).get("credits", float('inf'))
    credits_display = "Infinitos" if credits == float('inf') else str(int(credits))
    sidebar = render_template_string(sidebar_html, 
                                   username=session["username"], 
                                   credits=credits_display, 
                                   role="admin", 
                                   layout_settings=layout_settings_db)
    return render_template_string(ferramenta_html, 
                                sidebar=sidebar,
                                credits=credits_display, 
                                maintenance_mode=MAINTENANCE_MODE, 
                                layout_settings=layout_settings_db, 
                                username=session["username"], 
                                role="admin")

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
def toggle_block():
    if "username" not in session or session.get("role") not in ["admin", "simple", "master", "franchise"]:
        return jsonify({"success": False, "message": "Acesso negado!"})
    if client_id not in clients_db:
        return jsonify({"success": False, "message": "Cliente não encontrado!"})
    if session.get("role") != "admin" and clients_db[client_id]["owner"] != session["username"]:
        return jsonify({"success": False, "message": "Acesso negado! Este cliente não pertence a você!"})
    clients_db[client_id]["status"] = "blocked" if clients_db[client_id]["status"] == "active" else "active"
    save_db(clients_db, CLIENTS_DB_FILE)
    return jsonify({"success": True})

@app.route("/delete_client/<client_id>", methods=["POST"])
def delete_client():
    if "username" not in session or session.get("role") not in ["admin", "simple", "master", "franchise"]:
        return jsonify({"success": False, "message": "Acesso negado!"})
    if client_id not in clients_db:
        return jsonify({"success": False, "message": "Cliente não encontrado!"})
    if session.get("role") != "admin" and clients_db[client_id]["owner"] != session["username"]:
        return jsonify({"success": False, "message": "Acesso negado! Este cliente não pertence a você!"})
    
    owner = clients_db[client_id]["owner"]
    if owner in resellers_db:
        resellers_db[owner]["clients"].remove(client_id)
        save_db(resellers_db, RESELLERS_DB_FILE)
    
    del clients_db[client_id]
    save_db(clients_db, CLIENTS_DB_FILE)
    return jsonify({"success": True})

@app.route("/delete_reseller/<reseller_name>", methods=["POST"])
def delete_reseller():
    if "username" not in session or session.get("role") not in ["admin", "master", "franchise"]:
        return jsonify({"success": False, "message": "Acesso negado!"})
    if reseller_name not in resellers_db:
        return jsonify({"success": False, "message": "Revenda não encontrada!"})
    
    if session.get("role") != "admin":
        if resellers_db[reseller_name]["created_by"] != session["username"]:
            return jsonify({"success": False, "message": "Acesso negado! Esta revenda não foi criada por você!"})
    
    # Delete all clients of this reseller
    for client_id in resellers_db[reseller_name].get("clients", []):
        if client_id in clients_db:
            del clients_db[client_id]
    
    # Delete all sub-resellers
    for sub_reseller in resellers_db[reseller_name].get("sub_resellers", []):
        if sub_reseller in resellers_db:
            for client_id in resellers_db[sub_reseller].get("clients", []):
                if client_id in clients_db:
                    del clients_db[client_id]
            del resellers_db[sub_reseller]
    
    # Remove from creator's sub_resellers list
    creator = resellers_db[reseller_name]["created_by"]
    if creator in resellers_db:
        resellers_db[creator]["sub_resellers"].remove(reseller_name)
    
    del resellers_db[reseller_name]
    save_db(resellers_db, RESELLERS_DB_FILE)
    save_db(clients_db, CLIENTS_DB_FILE)
    return jsonify({"success": True})

@app.route("/get.php")
def get_m3u():
    username = request.args.get("username")
    password = request.args.get("password")
    output = request.args.get("output", "ts")  # Default to 'ts' for better compatibility
    if not username or not password:
        print(f"Invalid parameters: username={username}, password={password}")
        return "Parâmetros inválidos!", 400
    
    client_id = next((cid for cid, client in clients_db.items() if client["name"] == username and client["password"] == password and client["status"] == "active"), None)
    if not client_id:
        print(f"Invalid credentials or blocked client: username={username}, password={password}")
        return "Credenciais inválidas ou cliente bloqueado!", 403
    
    if datetime.now() > datetime.strptime(clients_db[client_id]["expiry_date"], "%Y-%m-%d"):
        clients_db[client_id]["status"] = "expired"
        save_db(clients_db, CLIENTS_DB_FILE)
        print(f"Access expired for client: username={username}")
        return "Acesso expirado!", 403
    
    global channels_cache
    if not channels_cache["data"] or (channels_cache["last_updated"] and (datetime.now() - channels_cache["last_updated"]) > CACHE_TIMEOUT):
        channels_cache["data"] = fetch_m3u(M3U_URL)
        channels_cache["last_updated"] = datetime.now()
    
    if not channels_cache["data"]:
        print(f"Failed to load channels for user {username}")
        return "Erro ao carregar a playlist!", 500
    
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
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@app.route("/player_api.php")
def player_api():
    username = request.args.get("username")
    password = request.args.get("password")
    action = request.args.get("action")
    
    if not username or not password:
        print(f"Missing username or password: username={username}, password={password}")
        return jsonify({"user_info": {"auth": 0}, "message": "Credenciais ausentes", "status": "error"}), 401
    
    client_id = next((cid for cid, client in clients_db.items() if client["name"] == username and client["password"] == password), None)
    if not client_id:
        print(f"Invalid credentials for player_api: username={username}, password={password}")
        return jsonify({"user_info": {"auth": 0}, "message": "Credenciais inválidas", "status": "error"}), 401
    
    client = clients_db[client_id]
    if client["status"] != "active":
        print(f"Client not active: username={username}, status={client['status']}")
        return jsonify({"user_info": {"auth": 0}, "message": "Cliente bloqueado ou expirado", "status": "error"}), 401
    
    expiry_date = datetime.strptime(client["expiry_date"], "%Y-%m-%d")
    if datetime.now() > expiry_date:
        client["status"] = "expired"
        save_db(clients_db, CLIENTS_DB_FILE)
        print(f"Client expired: username={username}, expiry_date={client['expiry_date']}")
        return jsonify({"user_info": {"auth": 0}, "message": "Acesso expirado", "status": "error"}), 401
    
    expiry_timestamp = int(expiry_date.timestamp())
    
    # Default response for missing or invalid action
    user_info = {
        "user_info": {
            "username": username,
            "password": password,
            "message": "Login successful",
            "auth": 1,
            "status": "Active",
            "exp_date": expiry_timestamp,
            "is_trial": 0,
            "active_cons": 0,
            "created_at": int(datetime.now().timestamp()),
            "max_connections": client["connections"],
            "allowed_output_formats": ["ts", "m3u8"]
        },
        "server_info": {
            "url": PUBLIC_URL,
            "port": "80",
            "rtmp_port": "0",
            "timezone": "America/Sao_Paulo",
            "time_now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    }
    
    if action == "get_user_info":
        print(f"Returning user info for username={username}")
        return jsonify(user_info)
    
    elif action == "get_live_categories":
        print(f"Returning live categories for username={username}")
        return jsonify([
            {"category_id": "1", "category_name": "Live TV", "parent_id": 0},
            {"category_id": "2", "category_name": "Outros", "parent_id": 0}
        ])
    
    elif action == "get_live_streams":
        global channels_cache
        if not channels_cache["data"] or (channels_cache["last_updated"] and (datetime.now() - channels_cache["last_updated"]) > CACHE_TIMEOUT):
            channels_cache["data"] = fetch_m3u(M3U_URL)
            channels_cache["last_updated"] = datetime.now()
        
        if not channels_cache["data"]:
            print(f"Failed to load channels for user {username}")
            return jsonify({"message": "Erro ao carregar canais", "status": "error"}), 500
        
        streams = []
        for channel in channels_cache["data"]:
            streams.append({
                "stream_id": channel["tvg_id"],
                "name": channel["name"],
                "logo": channel["logo"],
                "epg_channel_id": channel["tvg_id"],
                "category_id": "1" if channel["group"] != "Outros" else "2",
                "stream_type": "live",
                "stream_url": channel["url"],
                "added": str(int(datetime.now().timestamp())),
                "is_adult": 0
            })
        print(f"Returning {len(streams)} live streams for username={username}")
        return jsonify(streams)
    
    # Fallback for missing or invalid action
    print(f"Invalid or missing action for username={username}, action={action}")
    return jsonify(user_info)

@app.route("/xmltv.php")
def xmltv():
    username = request.args.get("username")
    password = request.args.get("password")
    client_id = next((cid for cid, client in clients_db.items() if client["name"] == username and client["password"] == password), None)
    if not client_id:
        print(f"Invalid credentials for xmltv: username={username}")
        return "Credenciais inválidas", 401
    xmltv_content = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n<tv></tv>'
    return Response(xmltv_content, mimetype="application/xml", headers={"Content-Disposition": "attachment; filename=epg.xml"})

if __name__ == "__main__":
    # Ensure admin exists in resellers_db for sub-resellers
    if "admin" not in resellers_db:
        resellers_db["admin"] = {
            "password": users_db["admin"]["password"],
            "credits": float('inf'),
            "infinite_credits": True,
            "email": "",
            "created_by": "system",
            "clients": [],
            "sub_resellers": [],
            "role": "admin"
        }
        save_db(resellers_db, RESELLERS_DB_FILE)
    
    channels_cache["data"] = fetch_m3u(M3U_URL)
    channels_cache["last_updated"] = datetime.now()
    print(f"Total de canais encontrados: {len(channels_cache['data'])}")
    app.run(debug=True, host="0.0.0.0", port=5000)