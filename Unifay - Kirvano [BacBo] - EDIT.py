import os
import nest_asyncio
import threading
import asyncio
import sqlite3
import traceback
from flask import Flask, request, jsonify
from cachetools import TTLCache
from telegram import Bot, Update
from telegram import InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from datetime import datetime, timedelta
from telegram.ext import CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from contextlib import contextmanager

# Carrega variáveis de ambiente
load_dotenv()

# Configuração de caminhos
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "database.db")

print(f"📂 O banco de dados será salvo em: {DATABASE_PATH}")

# Aqui vai a classe DatabaseManager
class DatabaseManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(DatabaseManager, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE,
                username TEXT,
                nome TEXT,
                email TEXT UNIQUE,
                data_entrada TEXT,
                data_expiracao TEXT,
                link_utilizado INTEGER DEFAULT 0,
                status TEXT,
                link_id TEXT,
                telegram_blocked INTEGER DEFAULT 0
            )
            ''')
            conn.commit()
            print("✅ Banco de dados configurado com sucesso!")

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(DATABASE_PATH, timeout=20)
        try:
            yield conn
        finally:
            conn.close()

    def execute_query(self, query, params=None, fetch=False):
        with self.get_connection() as conn:
            try:
                cursor = conn.cursor()
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                
                result = cursor.fetchall() if fetch else None
                conn.commit()
                return result
            except sqlite3.Error as e:
                conn.rollback()
                print(f"❌ Erro na query: {e}")
                print(f"Query: {query}")
                print(f"Params: {params}")
                raise
            finally:
                cursor.close()

    def insert_user(self, email, username, nome, telegram_id):
        """Insere um novo usuário com tratamento de concorrência"""
        try:
            email = email.lower().strip()  # Converter para minúsculas e remover espaços extras
            data_entrada = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data_expiracao = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

            
            with self._lock:  # Proteção extra para inserções simultâneas
                self.execute_query('''
                    INSERT INTO usuarios 
                    (email, username, nome, telegram_id, data_entrada, data_expiracao, 
                     link_utilizado, status, telegram_blocked)
                    VALUES (?, ?, ?, ?, ?, ?, 0, 'APPROVED', 0)
                ''', (email, username, nome, telegram_id, data_entrada, data_expiracao))
            
            print(f"✅ Usuário {email} inserido com sucesso!")
            return True
        except sqlite3.IntegrityError as e:
            print(f"⚠️ Erro de integridade ao inserir usuário: {e}")
            return False
        except Exception as e:
            print(f"❌ Erro ao inserir usuário: {e}")
            return False

    def get_user_by_email(self, email):
        """Busca um usuário pelo email"""
        try:
           email = email.lower().strip()  # Converter para minúsculas
           result = self.execute_query(
               "SELECT * FROM usuarios WHERE email = ?",
               (email,),
               fetch=True
            )
           return result[0] if result else None
        except sqlite3.Error as e:
           print(f"❌ Erro ao buscar usuário {email}: {e}")
           return None

    def update_user_status(self, email, status):
        """Atualiza o status de um usuário"""
        try:
            self.execute_query(
                "UPDATE usuarios SET status = ? WHERE email = ?",
                (status, email)
            )
            return True
        except sqlite3.Error as e:
            print(f"❌ Erro ao atualizar status do usuário {email}: {e}")
            return False

# Instância global do gerenciador de banco de dados
db_manager = DatabaseManager()

# Caches com diferentes TTLs
user_cache = TTLCache(maxsize=100, ttl=600)  # 10 minutos
stats_cache = TTLCache(maxsize=1, ttl=600)    # 10 minutos
active_cache = TTLCache(maxsize=1, ttl=600)   # 10 minutos
expired_cache = TTLCache(maxsize=1, ttl=600)  # 10 minutos

# Configurações do bot
TOKEN = os.getenv("TELEGRAM_TOKEN", "7673782621:AAF4xBp761u-JoQCm3dKc0i3P4BdaXMr09U")
CHANNEL_ID = os.getenv("CHANNEL_ID", "-1002171320926")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1335761360"))
bot = Bot(TOKEN)

# Dados temporários
temporary_data = {}

# Configuração do Flask
app = Flask(__name__)
nest_asyncio.apply()

# Função auxiliar para verificar se é admin
def is_admin(user_id):
    return user_id == ADMIN_ID





async def create_unique_invite_link():
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            member_limit=1,
            expire_date=int((datetime.now() + timedelta(hours=1)).timestamp())
        )
        return invite_link.invite_link
    except Exception as e:
        print(f"❌ Erro ao criar link: {e}")
        return None


async def revoke_invite_link(link_id):
    """
    Revoga um link de convite do Telegram com base no link_id fornecido.
    Caso haja erro, será tratado com exceções.
    """
    try:
        await asyncio.wait_for(
            bot.revoke_chat_invite_link(chat_id=CHANNEL_ID, invite_link=link_id),
            timeout=5  # Timeout de 10 segundos para revogar o link
        )
        print(f"Link revogado com sucesso: {link_id}")
    except asyncio.TimeoutError:
        print(f"Timeout ao revogar link: {link_id}")
    except Exception as e:
        print(f"Erro ao revogar link: {e}")




async def revoke_user_link(email):
    """
    Revoga o link de acesso do usuário
    """
    try:
        print(f"🔄 Iniciando revogação de acesso para {email}...")
        
        # Busca o link_id e informações do usuário
        user_info = db_manager.execute_query(
            'SELECT link_id, telegram_id, nome FROM usuarios WHERE email = ? AND link_id IS NOT NULL',
            (email,),
            fetch=True
        )
        
        if user_info and user_info[0][0]:
            link_id = user_info[0][0]
            telegram_id = user_info[0][1]
            nome = user_info[0][2]
            
            print(f"📝 Dados do usuário:\n"
                  f"👤 Nome: {nome}\n"
                  f"🔗 Link: {link_id}\n"
                  f"⏰ Horário: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Revoga o link
            print("⏳ Revogando link de convite...")
            await bot.revoke_chat_invite_link(
                chat_id=CHANNEL_ID,
                invite_link=link_id
            )
            
            # Atualiza o banco de dados
            db_manager.execute_query(
                'UPDATE usuarios SET link_id = NULL WHERE email = ?',
                (email,)
            )
            
            print(f"✅ Link revogado com sucesso para {email}")
            
            # Se tiver telegram_id, remove do canal
            if telegram_id:
                print(f"🔄 Iniciando remoção do canal...")
                await remove_user_from_channel(telegram_id)
        else:
            print(f"ℹ️ Nenhum link ativo encontrado para {email}")

    except Exception as e:
        print(f"❌ Erro ao revogar acesso de {email}:")
        print(f"Detalhes do erro: {str(e)}")
        traceback.print_exc()



async def _process_approved_sale(email, status):
    """Processa venda aprovada e insere no banco de dados SQLite"""
    print(f"🔍 Iniciando processamento de venda para {email}")
    print(f"📂 Caminho do banco de dados: {DATABASE_PATH}")

    # Normalizar status para manter compatibilidade
    if status == "COMPLETED":
        status = "APPROVED"

    try:
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        try:
            # Verificar se o usuário já existe
            cursor.execute("SELECT id FROM usuarios WHERE email = ?", (email,))
            existing_user = cursor.fetchone()

            data_entrada = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data_expiracao = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

            if existing_user:
                # Atualiza os dados do usuário se já existir
                cursor.execute('''
                    UPDATE usuarios 
                    SET status = ?, data_expiracao = ?, link_utilizado = 0, link_id = NULL
                    WHERE email = ?
                ''', (status, data_expiracao, email))
                print(f"✅ Usuário existente atualizado: {email} (Status: {status})")
            else:
                # Insere um novo usuário caso não exista
                cursor.execute('''
                    INSERT INTO usuarios (email, status, data_entrada, data_expiracao, link_utilizado, link_id, telegram_blocked)
                    VALUES (?, ?, ?, ?, 0, NULL, 0)
                ''', (email, status, data_entrada, data_expiracao))
                print(f"✅ Novo usuário inserido: {email} (Status: {status})")

            conn.commit()  # Confirma a transação
            print(f"💾 Transação confirmada para {email}")

        except sqlite3.Error as e:
            print(f"❌ Erro SQLite ao processar venda para {email}: {e}")
            print(f"Detalhes do erro: {traceback.format_exc()}")
            conn.rollback()  # Reverte a transação em caso de erro

        finally:
            cursor.close()
            conn.close()
            print(f"🔒 Conexão com o banco de dados fechada para {email}")

    except Exception as e:
        print(f"❌ Erro geral ao processar venda para {email}: {e}")
        print(f"Detalhes do erro: {traceback.format_exc()}")



@app.route('/bacbo-kirvano-unifay', methods=['POST'])
def webhook_handler():
    data = request.json
    if not data:
        print("❌ Payload vazio ou inválido")
        return jsonify({'error': 'Payload inválido'}), 400

    try:
        # Identifica a plataforma a partir da estrutura do payload
        event_type = data.get('event')
        email = data.get('client', {}).get('email', '').lower().strip() or data.get('customer', {}).get('email', '').lower().strip()
        status = data.get('transaction', {}).get('status') or data.get('status')

        # Identificação da plataforma com base na presença de campos específicos
        if 'client' in data:
            platform = 'unifaypay'
        elif 'customer' in data:
            platform = 'kirvano'
        else:
            print("❌ Plataforma desconhecida")
            return jsonify({'error': 'Plataforma desconhecida'}), 400

        if not all([event_type, status, email]):
            print("❌ Dados obrigatórios ausentes")
            return jsonify({'error': 'Dados incompletos'}), 400

        print(f"✅ Plataforma: {platform}, Evento: {event_type}, Status: {status}, Email: {email}")

        # Processa a venda independentemente do nome do produto
        asyncio.run(_process_approved_sale(email, status))

        # Mapeamento de eventos e processadores
        event_handlers = {
            "TRANSACTION_PAID": {
                "processor": _process_approved_sale,
                "message": None
            },
            "TRANSACTION_REFUNDED": {
                "processor": _process_cancellation,
                "message": (
                    "🚫 Olá {nome},\n\n"
                    "Identificamos que seu pagamento foi reembolsado.\n"
                    "Seu acesso ao canal VIP foi suspenso.\n\n"
                    "Dúvidas: @suporteralokwin"
                )
            },
            "TRANSACTION_CANCELED": {
                "processor": _process_cancellation,
                "message": (
                    "⚠️ Olá {nome},\n\n"
                    "Detectamos uma contestação de pagamento.\n"
                    "Seu acesso foi suspenso.\n\n"
                    "Contato: @suporteralokwin"
                )
            },
            "TRANSACTION_CHARGED_BACK": {
                "processor": _process_cancellation,
                "message": (
                    "⏰ Olá {nome},\n\n"
                    "Sua assinatura expirou.\n"
                    "Renove agora:\n"
                    "🛒 Direct.me/ralokadas"
                )
            },
            "SUBSCRIPTION_CANCELED": {
                "processor": _process_cancellation,
                "message": (
                    "📝 Olá {nome},\n\n"
                    "Sua assinatura foi cancelada conforme solicitado.\n"
                    "Para renovar:\n"
                    "🛒 Direct.me/ralokadas\n\n"
                    "Obrigado pela preferência!"
                )
            },
            "SUBSCRIPTION_RENEWED": {
                "processor": _process_renewal,
                "message": None
            },
            "SALE_APPROVED": {
                "processor": _process_approved_sale,
                "message": None
            },
            "SALE_REFUNDED": {
                "processor": _process_cancellation,
                "message": (
                    "🚫 Olá {nome},\n\n"
                    "Identificamos que seu pagamento foi reembolsado.\n"
                    "Seu acesso ao canal VIP foi suspenso.\n\n"
                    "Dúvidas: @suporteralokwin"
                )
            },
            "SALE_CHARGEBACK": {
                "processor": _process_cancellation,
                "message": (
                    "⚠️ Olá {nome},\n\n"
                    "Detectamos uma contestação de pagamento.\n"
                    "Seu acesso foi suspenso.\n\n"
                    "Contato: @suporteralokwin"
                )
            },
            "SUBSCRIPTION_EXPIRED": {
                "processor": _process_cancellation,
                "message": (
                    "⏰ Olá {nome},\n\n"
                    "Sua assinatura expirou.\n"
                    "Renove agora:\n"
                    "🛒 Direct.me/ralokadas"
                )
            }
        }

        # Verifica se o evento é suportado
        handler = event_handlers.get(event_type)
        if not handler:
            print(f"⚠️ Evento desconhecido: {event_type}")
            return jsonify({'error': f'Evento não suportado: {event_type}'}), 400

        # Executa o processador do evento
        asyncio.run(handler["processor"](email, status))

        # Envia notificação se houver mensagem configurada
        if handler["message"]:
            asyncio.run(send_status_notification(status, handler["message"].format(nome=data.get('client', {}).get('name', 'Usuário'))))

        return jsonify({'status': 'success', 'message': f'Evento {event_type} processado'})

    except Exception as e:
        print(f"❌ Erro no webhook: {e}")
        traceback.print_exc()
        return jsonify({'error': 'Erro interno'}), 500

    


async def remove_user_from_channel(telegram_id):
    """
    Remove um usuário do canal, revoga seus links e atualiza o banco de dados, mas preserva o telegram_id.
    """
    try:
        print(f"🔄 Iniciando remoção do usuário {telegram_id} do canal...")

        # Busca informações do usuário
        user_info = db_manager.execute_query(
            'SELECT email, nome FROM usuarios WHERE telegram_id = ?',
            (telegram_id,),
            fetch=True
        )

        if user_info:
            email = user_info[0][0]
            nome = user_info[0][1]
            print(f"👤 Usuário encontrado:\n"
                  f"📧 Email: {email}\n"
                  f"Nome: {nome}")

            # Atualiza o status do usuário
            db_manager.execute_query(
                '''
                UPDATE usuarios 
                SET link_utilizado = 0,
                    link_id = NULL, 
                    status = 'REVOKED'
                WHERE email = ?
                ''', 
                (email,)
            )
            print("✅ Dados atualizados no banco")

        # Remove do canal
        print("⏳ Removendo do canal...")
        try:
            await bot.ban_chat_member(
                chat_id=CHANNEL_ID, 
                user_id=telegram_id
            )
            
            # Pequena pausa para garantir que o ban foi processado
            await asyncio.sleep(1)
            
            await bot.unban_chat_member(
                chat_id=CHANNEL_ID,
                user_id=telegram_id
            )
            print(f"✅ Usuário {telegram_id} removido do canal com sucesso!")

        except Exception as e:
            print(f"❌ Erro ao remover do canal: {str(e)}")
            if "UserNotParticipant" in str(e):
                print("ℹ️ Usuário já não está no canal")
            elif "ChatAdminRequired" in str(e):
                print("❌ Bot não tem permissões de admin no canal")
            else:
                raise  # Re-lança outros tipos de erro

        print(f"✅ Processo de remoção concluído para {telegram_id}")

    except Exception as e:
        print(f"❌ Erro ao processar remoção do usuário {telegram_id}:")
        print(f"Detalhes do erro: {str(e)}")
        traceback.print_exc()



async def send_status_notification(status, message):
    """
    Envia notificação para usuários com um determinado status
    """
    try:
        # Busca usuários com o status específico que têm ID do Telegram
        users = db_manager.execute_query(
            ''' 
            SELECT telegram_id, nome, email 
            FROM usuarios 
            WHERE status = ? AND telegram_id IS NOT NULL
            AND telegram_blocked = 0
            ''',
            (status,),
            fetch=True
        )

        if not users:
            print(f"ℹ️ Nenhum usuário encontrado com status {status}")
            return

        success = 0
        failed = 0
        blocked_users = 0

        for user in users:
            telegram_id, nome, email = user
            try:
                await bot.send_message(
                    chat_id=telegram_id, 
                    text=message.format(nome=nome, email=email)
                )
                success += 1
                print(f"✅ Mensagem enviada para {email}")
            
            except Exception as e:
                if "bot was blocked" in str(e) or "chat not found" in str(e):
                    blocked_users += 1
                    db_manager.execute_query(
                        "UPDATE usuarios SET telegram_blocked = 1 WHERE telegram_id = ?",
                        (telegram_id,)
                    )
                    print(f"⚠️ Usuário {email} bloqueou o bot")
                else:
                    failed += 1
                    print(f"❌ Erro ao enviar para {email}: {e}")

        print(f"📢 Notificação {status}:\n"
              f"✅ Sucesso: {success}\n"
              f"❌ Falhas: {failed}\n"
              f"🚫 Bloqueados: {blocked_users}")

    except Exception as e:
        print(f"❌ Erro ao enviar notificações: {e}")
        traceback.print_exc()



async def _process_renewal(email, status):
    """
    Processa renovação de assinatura, atualizando a data de expiração
    """
    try:
        # Atualiza a data de expiração para mais 30 dias
        db_manager.execute_query(
            '''
            UPDATE usuarios 
            SET status = ?,
                data_expiracao = ?,
                link_utilizado = 0  -- Reseta o uso do link caso precise
            WHERE email = ?
            ''',
            (status, (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S'), email)
        )
        
        # Busca dados do usuário para notificação
        user = db_manager.execute_query(
            'SELECT telegram_id, nome FROM usuarios WHERE email = ?',
            (email,),
            fetch=True
        )
        
        if user and user[0][0]:
            # Envia mensagem de confirmação da renovação
            await bot.send_message(
                chat_id=user[0][0],
                text=(
                    f"🎉 Olá {user[0][1]}!\n\n"
                    f"Seu acesso continua liberado!\n\n"
                    f"Obrigado pela confiança! 🙏"
                )
            )
        
        print(f"✅ Renovação processada para {email}")
        
    except Exception as e:
        print(f"❌ Erro ao processar renovação para {email}: {e}")
        traceback.print_exc()



async def _process_cancellation(email, status):
    """
    Processa eventos de cancelamento com suporte a múltiplos tipos de notificação
    """
    try:
        # Revoga acesso existente
        await revoke_user_link(email)
        
        # Busca informações do usuário
        user = db_manager.execute_query(
            'SELECT telegram_id, nome FROM usuarios WHERE email = ?',
            (email,),
            fetch=True
        )
        
        if user and user[0][0]:
            await remove_user_from_channel(user[0][0])
        
        # Atualiza status do usuário
        db_manager.execute_query(
            '''
            UPDATE usuarios 
            SET status = ?, 
                link_utilizado = 0, 
                link_id = NULL
            WHERE email = ?
            ''',
            (status, email)
        )
        
        # Mapeamento de notificações por tipo de evento
        notificacoes = {
            "SALE_REFUNDED": (
                "🚫 Olá {nome},\n\n"
                "Identificamos que seu pagamento foi reembolsado. "
                "Por este motivo, seu acesso ao canal VIP foi suspenso.\n\n"
                "Em caso de dúvidas, entre em contato com nosso suporte @suporteralokwin."
            ),
            "SALE_CHARGEBACK": (
                "⚠️ Olá {nome},\n\n"
                "Detectamos uma contestação de pagamento (chargeback) vinculado à sua conta.\n\n"
                "Seu acesso ao canal VIP foi automaticamente suspenso.\n"
                "Entre em contato com nosso suporte para mais informações @suporteralokwin."
            ),
            "SUBSCRIPTION_EXPIRED": (
                "⏰ Olá {nome},\n\n"
                "Sua assinatura expirou. Para continuar acessando nosso canal VIP, "
                "por favor, renove sua assinatura.\n\n"
                "🛒 Acesse: Direct.me/ralokadas"
            ),
            "SUBSCRIPTION_CANCELED": (
                "📝 Olá {nome},\n\n"
                "Sua assinatura foi cancelada conforme solicitado.\n"
                "Caso queira renovar seu acesso no futuro:\n"
                "🛒 Acesse: Direct.me/ralokadas\n\n"
                "Obrigado por ter utilizado nossos serviços!"
            )
        }

        # Envia notificação se houver mensagem configurada para o status
        if status in notificacoes:
            await send_status_notification(status, notificacoes[status])
            print(f"✅ Notificação enviada para {email} - Status: {status}")
        
        print(f"✅ Cancelamento processado para {email} - Status: {status}")
            
    except Exception as e:
        print(f"❌ Erro ao processar cancelamento para {email}: {e}")
        traceback.print_exc()



async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Verifica se o usuário é válido
    if not update.effective_user:
        return

    chat_id = update.effective_user.id

    # Verifica se o usuário é o admin
    if chat_id == ADMIN_ID:
        # Painel do admin com os comandos disponíveis
        await update.message.reply_text(
            "🔑 Painel Admin:\n\n"
            "/textogeral - Mensagem para VIPs\n"
            "/stats - Estatísticas\n"
            "/buscar email - Buscar usuário\n"
            "/ban email - Banir acesso\n"
            "/unban email - Desbanir\n"
            "/lista - Listar VIPs ativos\n" 
            "/expirados - Ver expirados\n"
            "/limpar - Remover expirados"
        )
        return  # Retorna para evitar que o fluxo do usuário seja iniciado para admins

    # Fluxo normal para usuários
    username = update.effective_user.username or "Sem username"
    nome = update.effective_user.first_name or "Sem nome"

    # Salva os dados temporários do usuário
    temporary_data[chat_id] = {
        "id": chat_id,
        "username": username,
        "nome": nome,
        "step": "nome"
    }

    # Responde ao usuário solicitando o nome
    await update.message.reply_text("🤓 Olá, seja bem-vindo!\n\nMe diz qual o seu nome?")


async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Solicita ao admin o conteúdo para enviar um broadcast.
    """
    # Verifica se o usuário é válido e se é admin
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    # Informa ao admin como enviar a mensagem de broadcast
    await update.message.reply_text(
        "📝 Digite a mensagem de broadcast que deseja enviar. Você pode enviar:\n\n"
        "1️⃣ Texto com links\n"
        "2️⃣ Fotos com legenda\n"
        "3️⃣ Vídeos com legenda\n"
        "4️⃣ Documentos\n\n"
        "Exemplo de como enviar:\n"
        "- Texto: Basta escrever sua mensagem e enviá-la.\n"
        "- Foto/Vídeo/Documento: Envie a mídia diretamente após esta mensagem.\n\n"
        "Após enviar, a mensagem será replicada para todos os VIPs."
    )
    
    # Define que o admin está no modo de broadcast
    context.user_data['waiting_broadcast'] = True
    await update.message.reply_text("Por favor, envie o conteúdo que você deseja enviar para os VIPs.")


async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lida com a mensagem de broadcast enviada pelo admin.
    """
    if not context.user_data.get('waiting_broadcast'):
        return

    content_type = None
    content = None
    caption = update.message.caption

    # Determinar tipo de conteúdo
    if update.message.text:
        content_type = 'text'
        content = update.message.text
    elif update.message.photo:
        content_type = 'photo'
        content = update.message.photo
    elif update.message.video:
        content_type = 'video'
        content = update.message.video
    elif update.message.document:
        content_type = 'document'
        content = update.message.document
    else:
        await update.message.reply_text("❌ Tipo de mídia não suportado.")
        return

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    try:
        # Busca usuários que não bloquearam o bot
        cursor.execute('''
            SELECT telegram_id FROM usuarios 
            WHERE telegram_id IS NOT NULL AND (telegram_blocked IS NULL OR telegram_blocked = 0)
        ''')
        users = cursor.fetchall()
        success = 0
        failed = 0
        blocked_users = 0

        batch_size = 50  # Número de usuários a serem processados por vez
        total_users = len(users)

        for i in range(0, total_users, batch_size):
            batch = users[i:i + batch_size]
            await update.message.reply_text(f"🔄 Enviando: {i+1} até {min(i+batch_size, total_users)} de {total_users}")

            for user in batch:
                try:
                    if content_type == 'text':
                        await bot.send_message(chat_id=user[0], text=content)
                    
                    elif content_type == 'photo':
                        if len(content) > 1:
                            media_group = [
                                InputMediaPhoto(media=photo.file_id) 
                                for photo in content
                            ]
                            if caption:
                                media_group[0] = InputMediaPhoto(
                                    media=content[0].file_id, 
                                    caption=caption
                                )
                            await bot.send_media_group(chat_id=user[0], media=media_group)
                        else:
                            await bot.send_photo(chat_id=user[0], photo=content[-1].file_id, caption=caption)
                    
                    elif content_type == 'video':
                        await bot.send_video(chat_id=user[0], video=content.file_id, caption=caption)
                    
                    elif content_type == 'document':
                        await bot.send_document(chat_id=user[0], document=content.file_id, caption=caption)
                    
                    success += 1
                
                except Exception as e:
                    if "bot was blocked by the user" in str(e) or "chat not found" in str(e):
                        blocked_users += 1
                        cursor.execute('''
                            UPDATE usuarios 
                            SET telegram_blocked = 1 
                            WHERE telegram_id = ?
                        ''', (user[0],))
                        conn.commit()
                        print(f"⚠️ Usuário {user[0]} bloqueou o bot")
                    else:
                        failed += 1
                        print(f"❌ Erro ao enviar para usuário {user[0]}: {str(e)}")

            # Pequeno intervalo para evitar limites de taxa
            await asyncio.sleep(2)  # Aumentado para 2 segundos para maior segurança

        await update.message.reply_text(f"✅ Enviado:\nSucesso: {success}\nFalhas: {failed}\nBloqueados: {blocked_users}")

    except sqlite3.Error as e:
        print(f"❌ Erro no banco de dados durante o broadcast: {e}")
        await update.message.reply_text("❌ Ocorreu um erro no banco de dados durante o envio do broadcast.")

    finally:
        cursor.close()
        conn.close()
        context.user_data['waiting_broadcast'] = False



async def monitor_bot():
    """Verifica se o bot está funcionando corretamente."""
    while True:
        try:
            # Aqui você pode implementar uma verificação simples, como enviar uma mensagem de teste
            await bot.send_message(chat_id=ADMIN_ID, text="✅ O bot está funcionando corretamente!")
        except Exception as e:
            print(f"❌ Erro ao verificar o bot: {e}")
        
        await asyncio.sleep(3600)  # Verifica a cada hora



async def get_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Obtém estatísticas gerais de usuários (ativos, expirados e total).
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
    
    try:
        # Verifica cache
        if 'stats' in stats_cache:
            stats = stats_cache['stats']
        else:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            try:
                # Contar usuários com status 'APPROVED'
                cursor.execute("SELECT COUNT(*) FROM usuarios WHERE status = 'APPROVED'")
                ativos = cursor.fetchone()[0]
                
                # Contar usuários com data de expiração anterior ao momento atual
                cursor.execute("SELECT COUNT(*) FROM usuarios WHERE data_expiracao < ?", (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
                expirados = cursor.fetchone()[0]
                
                # Contar o total de usuários
                cursor.execute("SELECT COUNT(*) FROM usuarios")
                total = cursor.fetchone()[0]
                
                stats = {'ativos': ativos, 'expirados': expirados, 'total': total}
                stats_cache['stats'] = stats
            finally:
                cursor.close()
                conn.close()

        await update.message.reply_text(
            f"📊 Estatísticas:\n\n"
            f"👥 Total: {stats['total']}\n"
            f"✅ Ativos: {stats['ativos']}\n"
            f"❌ Expirados: {stats['expirados']}"
        )
    except sqlite3.Error as e:
        print(f"❌ Erro ao obter estatísticas no banco de dados: {e}")
        await update.message.reply_text("❌ Erro ao processar as estatísticas.")
    except Exception as e:
        print(f"❌ Erro inesperado ao obter estatísticas: {e}")
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao processar as estatísticas.")


async def search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Busca informações de um usuário pelo e-mail.
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    # Verificar se o e-mail foi fornecido
    if len(context.args) == 0:
        await update.message.reply_text("⚠️ Por favor, forneça o e-mail do usuário. Exemplo: `/buscar email@exemplo.com`")
        return
    
    try:
        email = context.args[0]
        conn = sqlite3.connect(DATABASE_PATH)
        cursor = conn.cursor()

        cursor.execute(''' 
            SELECT nome, telegram_id, username, data_entrada, data_expiracao, status
            FROM usuarios 
            WHERE email = ?
        ''', (email,))
        user = cursor.fetchone()

        if user:
            nome, tid, username, entrada, expira, status = user
            await update.message.reply_text(
                f"🔍 Detalhes do Usuário 🔍\n\n"
                f"📧 E-mail: `{email}`\n"
                f"👤 Nome: {nome or 'Não informado'}\n"
                f"🆔 ID do Telegram: {tid or 'Não associado'}\n"
                f"💬 Username: @{username or 'Não associado'}\n"
                f"📅 Entrada: {entrada or 'Não registrada'}\n"
                f"⏳ Expiração: {expira or 'Sem data de expiração'}\n"
                f"📌 Status: `{status}`"
            )
        else:
            await update.message.reply_text("❌ Nenhum usuário foi encontrado com o e-mail fornecido.")

        # Limpar o estado de temporary_data para garantir que o bot não continue esperando informações
        if update.effective_user.id in temporary_data:
            del temporary_data[update.effective_user.id]  # Remover o administrador da memória temporária

    except sqlite3.Error as e:
        print(f"❌ Erro no banco de dados ao buscar usuário {email}: {e}")
        await update.message.reply_text("❌ Erro ao processar a busca no banco de dados.")
    except Exception as e:
        print(f"❌ Erro inesperado ao buscar usuário {email}: {e}")
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao processar a busca.")
    finally:
        cursor.close()
        conn.close()




async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Bane um usuário pelo e-mail e remove acesso ao canal.
    """
    # Verifica se o comando foi chamado por um administrador
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    # Verifica se o comando foi chamado com o argumento necessário (email)
    if len(context.args) == 0:
        await update.message.reply_text("⚠️ Por favor, forneça o e-mail do usuário.")
        return

    email = context.args[0]

    try:
        # Busca o usuário no banco de dados
        user = db_manager.get_user_by_email(email)
        
        if user:
            telegram_id = user[1]  # Supondo que o telegram_id seja o segundo elemento

            # Revoga o link e remove o usuário do canal
            await revoke_user_link(email)
            if telegram_id:
                await remove_user_from_channel(telegram_id)

            # Atualiza o status do usuário no banco de dados para "BANNED"
            db_manager.update_user_status(email, 'BANNED')

            # Envia mensagem ao usuário banido
            if telegram_id:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=(
                        "🚫 Você foi banido do canal.\n\n"
                        "Se você acha que isso foi um engano, entre em contato com o suporte: @suporteralokwin"
                    )
                )

            await update.message.reply_text(f"✅ Usuário com e-mail {email} foi banido com sucesso!")
        else:
            await update.message.reply_text(f"❌ Usuário com e-mail {email} não encontrado no banco de dados.")

    except sqlite3.Error as e:
        await update.message.reply_text(f"❌ Erro ao tentar banir o usuário no banco de dados: {str(e)}")
    except Exception as e:
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao tentar banir o usuário.")
        print(f"❌ Erro inesperado: {str(e)}")




async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adiciona um usuário manualmente ao banco de dados"""
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    # Verifica se o e-mail foi fornecido no comando
    if len(context.args) == 0:
        await update.message.reply_text("⚠️ Uso correto: `/adduser email@exemplo.com`")
        return

    email = context.args[0].lower().strip()  # Converte para minúsculas

    try:
        # Verifica se o usuário já existe no banco
        existing_user = db_manager.get_user_by_email(email)
        
        if existing_user:
            await update.message.reply_text(f"⚠️ O usuário `{email}` já está cadastrado no banco de dados.")
            return

        # Dados padrão do novo usuário
        data_entrada = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data_expiracao = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        
        # Adiciona o usuário ao banco
        db_manager.execute_query('''
            INSERT INTO usuarios (email, status, data_entrada, data_expiracao, link_utilizado, link_id, telegram_blocked)
            VALUES (?, 'APPROVED', ?, ?, 0, NULL, 0)
        ''', (email, data_entrada, data_expiracao))

        await update.message.reply_text(f"✅ Usuário `{email}` foi adicionado com sucesso por 30 dias!")

    except Exception as e:
        print(f"❌ Erro ao adicionar usuário: {e}")
        await update.message.reply_text("❌ Erro ao adicionar usuário. Tente novamente.")




async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Desbane um usuário e envia novo link de acesso
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        return
        
    if len(context.args) == 0:
        await update.message.reply_text("⚠️ Por favor, forneça o email do usuário.")
        return

    email = context.args[0]

    try:
        # Busca dados do usuário
        user = db_manager.execute_query(
            "SELECT telegram_id, nome FROM usuarios WHERE email = ?",
            (email,),
            fetch=True
        )

        if user:
            telegram_id, nome = user[0]

            # Atualiza status e dados do usuário
            nova_data = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
            db_manager.execute_query(
                '''
                UPDATE usuarios 
                SET status = 'APPROVED',
                    link_utilizado = 0,
                    link_id = NULL,
                    data_expiracao = ?
                WHERE email = ?
                ''',
                (nova_data, email)
            )

            # Gera novo link
            invite_link = await create_unique_invite_link()
            if invite_link:
                if telegram_id:
                    try:
                        # Mensagem sem formatação Markdown
                        mensagem = (
                            f"🎉 Parabéns, {nome}!\n\n"
                            f"Sua conta foi reativada com sucesso! Aqui está o seu link de acesso exclusivo:\n\n"
                            f"{invite_link}\n\n"
                            f"⚠️ Este link é válido para um único uso e expira em 1 hora."
                        )
                        
                        await context.bot.send_message(
                            chat_id=telegram_id,
                            text=mensagem
                        )
                        
                        # Atualiza o link no banco
                        db_manager.execute_query(
                            "UPDATE usuarios SET link_id = ? WHERE email = ?",
                            (invite_link, email)
                        )
                        
                        await update.message.reply_text(
                            f"✅ Usuário {email} foi desbanido e recebeu um novo link de acesso!"
                        )
                    except Exception as e:
                        await update.message.reply_text(
                            f"⚠️ Usuário desbanido, mas não foi possível enviar a mensagem: {str(e)}"
                        )
                else:
                    await update.message.reply_text(
                        f"✅ Usuário {email} foi desbanido, mas não tem Telegram ID associado."
                    )
            else:
                await update.message.reply_text(
                    "❌ Usuário desbanido, mas ocorreu um erro ao gerar o link."
                )
        else:
            await update.message.reply_text(
                f"❌ Usuário {email} não encontrado no banco de dados."
            )

    except Exception as e:
        print(f"❌ Erro ao desbanir usuário: {str(e)}")
        traceback.print_exc()
        await update.message.reply_text(
            f"❌ Erro ao processar o desban: {str(e)}"
        )


active_cache = TTLCache(maxsize=1, ttl=120)   # 2 minutos
expired_cache = TTLCache(maxsize=1, ttl=120)  # 2 minutos

async def list_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lista os VIPs ativos com status 'APPROVED'.
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    try:
        if 'active_users' in active_cache:
            users = active_cache['active_users']
        else:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            try:
                # Consulta usuários com status 'APPROVED' e ordena pela data de expiração
                cursor.execute('''
                    SELECT email, nome, data_expiracao 
                    FROM usuarios 
                    WHERE status = 'APPROVED' 
                    ORDER BY data_expiracao
                ''')
                users = cursor.fetchall()
                active_cache['active_users'] = users
            finally:
                cursor.close()
                conn.close()

        if users:
            batch_size = 10  # Número de usuários a serem enviados por vez
            total_users = len(users)

            for i in range(0, total_users, batch_size):
                batch = users[i:i + batch_size]
                msg = "📋 VIPs Ativos:\n\n"
                for email, nome, expira in batch:
                    msg += f"📧 {email}\n👤 {nome or 'Sem Nome'}\n📅 Expira: {expira}\n\n"
                
                await update.message.reply_text(msg)
                await asyncio.sleep(60)  # Delay para evitar limites de taxa

        else:
            await update.message.reply_text("❌ Nenhum VIP ativo!")
    except sqlite3.Error as e:
        print(f"❌ Erro ao listar VIPs ativos no banco de dados: {e}")
        await update.message.reply_text("❌ Erro ao listar VIPs ativos.")
    except Exception as e:
        print(f"❌ Erro inesperado ao listar ativos: {e}")
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao listar VIPs ativos.")



async def list_expired(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Lista as assinaturas expiradas.
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    try:
        if 'expired_users' in expired_cache:
            users = expired_cache['expired_users']
        else:
            conn = sqlite3.connect(DATABASE_PATH)
            cursor = conn.cursor()
            try:
                # Consulta usuários com data de expiração anterior ao momento atual
                cursor.execute('''
                    SELECT email, nome, data_expiracao 
                    FROM usuarios 
                    WHERE data_expiracao < ?
                    ORDER BY data_expiracao
                ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
                users = cursor.fetchall()
                expired_cache['expired_users'] = users
            finally:
                cursor.close()
                conn.close()

        if users:
            batch_size = 10  # Número de usuários a serem enviados por vez
            total_users = len(users)

            for i in range(0, total_users, batch_size):
                batch = users[i:i + batch_size]
                msg = "📋 Assinaturas Expiradas:\n\n"
                for email, nome, expira in batch:
                    msg += f"📧 {email}\n👤 {nome or 'Sem Nome'}\n📅 Expirou: {expira}\n\n"
                
                await update.message.reply_text(msg)
                await asyncio.sleep(1)  # Delay para evitar limites de taxa

        else:
            await update.message.reply_text("✅ Nenhuma assinatura expirada!")
    except sqlite3.Error as e:
        print(f"❌ Erro ao listar assinaturas expiradas no banco de dados: {e}")
        await update.message.reply_text("❌ Erro ao listar assinaturas expiradas.")
    except Exception as e:
        print(f"❌ Erro inesperado ao listar expirados: {e}")
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao listar assinaturas expiradas.")
        



async def clear_expired(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Remove usuários com assinaturas expiradas do canal, revoga links e atualiza o banco de dados.
    """
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    try:
        # Seleciona os usuários com data de expiração anterior ao momento atual
        cursor.execute(''' 
            SELECT telegram_id, email, nome 
            FROM usuarios 
            WHERE data_expiracao < ?
        ''', (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        users = cursor.fetchall()

        removed = 0
        failed_removals = []

        # Se não houver usuários expirados
        if not users:
            await update.message.reply_text("ℹ️ Nenhum usuário expirado encontrado.")
            return

        # Processamento em lotes
        batch_size = 10  # Número de usuários a serem removidos por vez
        for i in range(0, len(users), batch_size):
            batch = users[i:i + batch_size]
            for user in batch:
                telegram_id, email, nome = user
                try:
                    # Envia mensagem para o usuário expirado
                    if telegram_id:
                        await bot.send_message(
                            chat_id=telegram_id,
                            text=(
                                f"⏰ Olá {nome},\n\n"
                                "Sua assinatura expirou. Para continuar acessando nosso canal VIP, "
                                "por favor, renove sua assinatura.\n\n"
                                "🛒 Acesse: https://direct.me/ralokadas"
                            )
                        )

                    # Revoga o link de convite
                    cursor.execute(''' 
                        SELECT link_id 
                        FROM usuarios 
                        WHERE email = ?
                    ''', (email,))
                    link_id = cursor.fetchone()
                    if link_id and link_id[0]:
                        await bot.revoke_chat_invite_link(
                            chat_id=CHANNEL_ID,
                            invite_link=link_id[0]
                        )

                    # Remove do canal
                    if telegram_id:
                        await remove_user_from_channel(telegram_id)
                    
                    # Atualiza o status no banco de dados para expirado
                    cursor.execute(''' 
                        UPDATE usuarios 
                        SET status = 'EXPIRED', 
                            telegram_blocked = 1,
                            link_id = NULL,
                            link_utilizado = 0
                        WHERE email = ?
                    ''', (email,))
                    
                    removed += 1
                    print(f"✅ Usuário {email} ({nome}) removido por expiração")
                
                except Exception as e:
                    failed_removals.append((email, nome, str(e)))
                    print(f"❌ Erro ao remover o usuário {email}: {str(e)}")
                    continue

            # Delay entre os lotes para evitar congestionamento
            await asyncio.sleep(2)

        # Commit das alterações no banco de dados
        conn.commit()

        # Limpar o cache de usuários expirados
        expired_cache.clear()

        # Mensagem de resultado com detalhes
        response_message = f"✅ {removed} usuários expirados removidos e notificados!"
        
        if failed_removals:
            response_message += "\n\n⚠️ Falhas de remoção:"
            for email, nome, error in failed_removals:
                response_message += f"\n- {nome} ({email}): {error}"

        await update.message.reply_text(response_message)

    except sqlite3.Error as e:
        print(f"❌ Erro no banco de dados ao limpar expirados: {e}")
        await update.message.reply_text("❌ Ocorreu um erro ao limpar usuários expirados.")
        conn.rollback()

    except Exception as e:
        print(f"❌ Erro inesperado ao limpar usuários expirados: {e}")
        await update.message.reply_text("❌ Ocorreu um erro inesperado ao limpar usuários expirados.")
        conn.rollback()

    finally:
        cursor.close()
        conn.close()



async def handle_admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Verifica se o admin está executando o comando
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Você não tem permissão para executar esse comando.")
        return

    # Verifica se o comando tem argumentos
    if context.args:
        # Lógica para comandos do admin com base no primeiro argumento
        if context.args[0] == "buscar":
            # Ação de buscar um usuário
            await search_user(update, context)
        elif context.args[0] == "ban":
            # Ação de banir um usuário
            await ban_user(update, context)
        elif context.args[0] == "unban":
            # Ação de desbanir um usuário
            await unban_user(update, context)
        elif context.args[0] == "lista":
            # Ação de listar todos os usuários ativos
            await list_active(update, context)
        elif context.args[0] == "expirados":
            # Ação de listar todos os usuários expirados
            await list_expired(update, context)
        elif context.args[0] == "limpar":
            # Ação de limpar usuários expirados
            await clear_expired(update, context)
        elif context.args[0] == "stats":
            # Ação de exibir as estatísticas do sistema
            await get_stats(update, context)
        else:
            # Caso o comando não seja reconhecido
            await update.message.reply_text("❌ Comando desconhecido. Tente novamente com um comando válido.")
    else:
        # Se o comando não fornecer parâmetros, avisa o admin
        await update.message.reply_text("❌ Comando inválido. Por favor, forneça o parâmetro necessário. Exemplo: /ban email")




async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Manipula mensagens enviadas ao bot, verificando se são de administradores
    ou usuários comuns.
    """
    if not update.message or not update.effective_user:
        return

    chat_id = update.effective_user.id

    # Verificação de comando exclusivo para administradores
    if chat_id == ADMIN_ID:
        if context.user_data.get('waiting_broadcast'):
            context.user_data['waiting_broadcast'] = False
            try:
                # Busca usuários não bloqueados
                users = db_manager.execute_query(
                    'SELECT telegram_id FROM usuarios WHERE telegram_id IS NOT NULL AND telegram_blocked = 0',
                    fetch=True
                )

                if not users:
                    await update.message.reply_text("❌ Nenhum usuário encontrado para enviar a mensagem.")
                    return

                success = 0
                failed = 0
                blocked = 0

                batch_size = 50
                total_users = len(users)

                for i in range(0, total_users, batch_size):
                    batch = users[i:i + batch_size]
                    await update.message.reply_text(
                        f"🔄 Enviando: {i+1} até {min(i+batch_size, total_users)} de {total_users}"
                    )

                    for user in batch:
                        try:
                            if update.message.text:
                                await context.bot.send_message(
                                    chat_id=user[0], 
                                    text=update.message.text
                                )
                            elif update.message.photo:
                                await context.bot.send_photo(
                                    chat_id=user[0], 
                                    photo=update.message.photo[-1].file_id, 
                                    caption=update.message.caption
                                )
                            elif update.message.video:
                                await context.bot.send_video(
                                    chat_id=user[0], 
                                    video=update.message.video.file_id, 
                                    caption=update.message.caption
                                )
                            elif update.message.document:
                                await context.bot.send_document(
                                    chat_id=user[0], 
                                    document=update.message.document.file_id, 
                                    caption=update.message.caption
                                )
                            success += 1
                        except Exception as e:
                            if "bot was blocked by the user" in str(e):
                                blocked += 1
                                db_manager.execute_query(
                                    "UPDATE usuarios SET telegram_blocked = 1 WHERE telegram_id = ?",
                                    (user[0],)
                                )
                            else:
                                failed += 1
                            print(f"❌ Erro ao enviar para {user[0]}: {str(e)}")

                    await asyncio.sleep(1)

                await update.message.reply_text(
                    f"✅ Broadcast finalizado:\n"
                    f"Sucesso: {success}\n"
                    f"Falhas: {failed}\n"
                    f"Bloqueados: {blocked}"
                )

            except Exception as e:
                print(f"❌ Erro no broadcast: {str(e)}")
                await update.message.reply_text("❌ Erro ao processar o broadcast.")
            return

        await update.message.reply_text("🔒 Você está no modo admin. Use os comandos apropriados.")
        return

    # Fluxo para usuários normais
    user_data = temporary_data.get(chat_id)
    if not user_data:
        await update.message.reply_text("Por favor, para retornar ao menu, digite /start.")
        return

    # Processamento do nome
    if user_data["step"] == "nome":
        nome = update.message.text
        user_data["nome"] = nome
        user_data["step"] = "email"
        await update.message.reply_text(
            f"👀 Ei {nome}!\n\n"
            f"📧 Agora, por favor, informe o e-mail que você utilizou na compra:"
        )
        return

    # Processamento do email
    if user_data["step"] == "email":
        email = update.message.text.lower().strip()
        try:
            # Verifica se o email já está associado a outro Telegram ID
            existing_user = db_manager.execute_query(
                "SELECT telegram_id FROM usuarios WHERE email = ?",
                (email,),
                fetch=True
            )

            if existing_user and existing_user[0][0]:
                if existing_user[0][0] != chat_id:
                    await update.message.reply_text(
                        "❌ **Este email já está vinculado a outro usuário do Telegram.**\n\n"
                        "Se precisar de ajuda, entre em contato com o suporte.\n\n"
                        "🔗 Para mais informações ou para realizar uma nova compra, visite:\n"
                        "🌐 [Direct.me/ralokadas](https://direct.me/ralokadas)\n\n"
                        "**Estamos aqui para ajudar você a ter a melhor experiência possível!**"
                    )
                    return

            # Verifica se o usuário já tem um link ativo
            user_data_db = db_manager.execute_query(
                "SELECT link_id, data_expiracao FROM usuarios WHERE email = ?",
                (email,),
                fetch=True
            )

            if user_data_db and user_data_db[0][0]:
                invite_link = user_data_db[0][0]
                await update.message.reply_text(
                    f"🔗 Você já tem um link ativo. Aqui está novamente:\n{invite_link}"
                )
                return

            # Se chegou aqui, cria um novo link
            invite_link = await create_unique_invite_link()
            if invite_link:
                db_manager.execute_query(
                    '''
                    UPDATE usuarios 
                    SET telegram_id = ?,
                        nome = ?,
                        username = ?,
                        link_utilizado = 0,
                        data_entrada = ?,
                        link_id = ?,
                        telegram_blocked = 0
                    WHERE email = ?
                    ''',
                    (chat_id, user_data["nome"], user_data.get("username"),
                     datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                     invite_link, email)
                )
                await update.message.reply_text(
                    f"🥳 **Você comprou com sucesso!**\n\n"
                    "🔗 Aqui está o seu link exclusivo para acesso ao VIP:\n"
                    f"{invite_link}\n\n"
                    "⚠️ **Este link é válido para um único uso e expira em 1 hora.**\n\n"
                    "Se você tiver alguma dúvida ou precisar de assistência, não hesite em entrar em contato com o suporte! @suporteralokwin"
                )
            else:
                await update.message.reply_text("❌ Erro ao gerar o link de convite.")
        
        except Exception as e:
            print(f"❌ Erro ao processar usuário: {str(e)}")
            await update.message.reply_text(
                "❌ Ocorreu um erro ao processar sua solicitação.\n"
                "Por favor, tente novamente mais tarde."
            )

        finally:
            del temporary_data[chat_id]



# Fila de usuários aguardando aprovação
waiting_users = []
# Variável global para armazenar os membros anteriores
last_members = set()

# Função para verificar se o usuário tem acesso
async def check_user_access(telegram_id):
    user = db_manager.execute_query(
        "SELECT * FROM usuarios WHERE telegram_id = ? AND status = 'APPROVED' AND telegram_blocked = 0 AND removido = 0",
        (telegram_id,),
        fetch=True
    )
    return bool(user)

# Função para notificar o admin sobre o novo usuário
async def notify_admin_new_user(bot, telegram_id, username, first_name):
    admin_chat_id = ADMIN_ID
    keyboard = [
        [
            InlineKeyboardButton("✅ Sim", callback_data=f"remove_{telegram_id}"),
            InlineKeyboardButton("❌ Não", callback_data=f"ignore_{telegram_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = (
        f"👤 **Novo usuário detectado!**\n\n"
        f"🆔 ID: `{telegram_id}`\n"
        f"👤 Nome: {first_name or 'Desconhecido'}\n"
        f"💬 Username: @{username if username else 'Nenhum'}\n\n"
        "🚨 Este usuário **NÃO TEM ACESSO** ao VIP. Deseja removê-lo?"
    )
    await bot.send_message(chat_id=admin_chat_id, text=message, reply_markup=reply_markup, parse_mode="Markdown")

# Callback para tratar a resposta do admin
async def handle_admin_response(update, context):
    query = update.callback_query
    telegram_id = int(query.data.split("_")[1])

    if query.data.startswith("remove"):
        action = "remover"
    else:
        action = "ignorar"

    # Aqui, podemos processar a ação de remoção ou ignorar o usuário
    print(f"Admin escolheu {action} o usuário com ID {telegram_id}")

    # Agora, removemos ou ignoramos o usuário conforme a escolha do admin
    if waiting_users:
        # Envia o próximo usuário na fila
        next_user = waiting_users.pop(0)
        user = await context.bot.get_chat_member(CHANNEL_ID, next_user)
        await notify_admin_new_user(context.bot, next_user, user.user.username, user.user.first_name)
    else:
        # Se não houver mais usuários, envia uma mensagem
        await context.bot.send_message(ADMIN_ID, "🚫 **Sem nenhum usuário intruso para verificar.**")

    # Respondendo ao callback
    await query.answer()

# Monitorar novos membros
async def monitor_new_members(bot):
    global last_members  # Garantir que estamos atualizando a variável global

    print("👀 Monitorando novos membros em tempo real...")

    while True:
        try:
            chat_members = await bot.get_chat_administrators(CHANNEL_ID)
            current_members = {member.user.id for member in chat_members}

            # Identifica novos usuários
            new_users = current_members - last_members
            if new_users:
                for telegram_id in new_users:
                    if telegram_id == bot.id:
                        continue  # Evita que o bot tente se remover
                    
                    user = await bot.get_chat_member(CHANNEL_ID, telegram_id)
                    
                    # Adiciona o usuário à fila
                    waiting_users.append(telegram_id)

                    if len(waiting_users) == 1:
                        # Só envia a notificação se for o primeiro na fila
                        await notify_admin_new_user(bot, telegram_id, user.user.username, user.user.first_name)
                    await asyncio.sleep(2)  # Delay para evitar sobrecarga na API

            # Atualiza a lista para a próxima checagem
            last_members = current_members

        except Exception as e:
            print(f"❌ Erro ao monitorar novos membros: {e}")

        await asyncio.sleep(10)  # Checa novos membros a cada 10 segundos


# Função para verificar novos membros ao rodar o comando /check
async def check_new_members(update, context):
    """Verifica se há novos membros no canal e se estão no banco de dados."""
    global last_members  # Garantir que estamos usando a variável global

    chat_members = await context.bot.get_chat_administrators(CHANNEL_ID)
    current_members = {member.user.id for member in chat_members}

    # Verifica se há novos membros desde a última verificação
    new_users = current_members - last_members

    if not new_users:
        await update.message.reply_text("Nenhum novo membro encontrado.")
        return

    for telegram_id in new_users:
        if telegram_id == context.bot.id:
            continue  # Evita que o bot tente se remover
        
        # Verifica se o usuário está no banco de dados
        user = db_manager.execute_query(
            "SELECT * FROM usuarios WHERE telegram_id = ? AND status = 'APPROVED' AND telegram_blocked = 0 AND removido = 0",
            (telegram_id,),
            fetch=True
        )
        
        if not user:
            # Se o usuário não estiver no banco de dados, notifica o admin
            await notify_admin_new_user(context.bot, telegram_id, None, None)

    # Atualiza o estado para a próxima verificação
    last_members = current_members  # Atualiza a variável para a próxima verificação
    await update.message.reply_text(f"Verificação concluída. {len(new_users)} novo(s) usuário(s) encontrado(s).")


# Aqui é onde o Flask irá rodar
def run_flask():
    """Função para rodar o Flask em um thread separado."""
    app.run(host='0.0.0.0', port=5000, use_reloader=False, threaded=True)

# Função principal para iniciar o bot
async def iniciar_bot():
    """Função para iniciar o bot do Telegram."""
    nest_asyncio.apply()  # Permite que o asyncio funcione com o Flask

    # Configuração do bot
    application = Application.builder().token(TOKEN).build()

    # Iniciar o monitoramento de membros do canal
    asyncio.create_task(monitor_new_members(application.bot))

    # Adicionando o handler para a resposta do admin
    application.add_handler(CallbackQueryHandler(handle_admin_response))

    # Adiciona o comando /check
    application.add_handler(CommandHandler('check', check_new_members))

    # Comandos básicos
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('textogeral', send_broadcast))
    
    # Comandos admin
    application.add_handler(CommandHandler('stats', get_stats))
    application.add_handler(CommandHandler('adduser', add_user))
    application.add_handler(CommandHandler('buscar', search_user))
    application.add_handler(CommandHandler('ban', ban_user))
    application.add_handler(CommandHandler('unban', unban_user))
    application.add_handler(CommandHandler('lista', list_active))
    application.add_handler(CommandHandler('expirados', list_expired))
    application.add_handler(CommandHandler('limpar', clear_expired))
    
    # Handler de mensagens comuns
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Handler para mídia no broadcast
    application.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.TEXT, 
        handle_broadcast_message
    ))

    # Iniciar o polling do bot
    await application.run_polling()

# Função principal
if __name__ == '__main__':
    # Rodando o Flask em um thread separado
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Iniciar o bot com asyncio
    try:
        asyncio.run(iniciar_bot())
    except Exception as e:
        print(f"❌ Erro ao iniciar o bot: {e}")