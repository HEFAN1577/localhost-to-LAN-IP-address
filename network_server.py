from http.server import HTTPServer, SimpleHTTPRequestHandler
import socket
import webbrowser
import sys
from socketserver import ThreadingMixIn
import threading
import http.client
import json
import asyncio
import websockets
import concurrent.futures
from urllib.parse import urljoin
import struct
import base64
import hashlib
import tkinter as tk
from tkinter import ttk, scrolledtext
import queue
import datetime
from PIL import Image, ImageTk
import os

# 创建一个全局消息队列用于线程间通信
message_queue = queue.Queue()

class RedirectText:
    def __init__(self, queue):
        self.queue = queue

    def write(self, string):
        self.queue.put(string)

    def flush(self):
        pass

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """处理多线程的HTTP服务器"""
    allow_reuse_address = True
    def __init__(self, server_address, RequestHandlerClass):
        self.stop_flag = threading.Event()
        super().__init__(server_address, RequestHandlerClass)

    def serve_forever(self, poll_interval=0.5):
        while not self.stop_flag.is_set():
            self._BaseServer__is_shut_down.clear()
            try:
                self._handle_request_noblock()
            except:
                pass
            self.service_actions()

    def stop(self):
        self.stop_flag.set()
        self.server_close()

class ProxyHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, internal_port=8188, **kwargs):
        self.internal_port = internal_port
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.headers.get('Upgrade') == 'websocket':
            self.handle_websocket()
        else:
            self.proxy_request('GET')
        
    def do_POST(self):
        self.proxy_request('POST')

    def handle_websocket(self):
        try:
            internal_ws_url = f'ws://127.0.0.1:8188{self.path}'
            print(f"尝试连接到内部 WebSocket: {internal_ws_url}")

            # WebSocket 握手
            if not self.headers.get('Sec-WebSocket-Key'):
                self.send_error(400, 'Missing Sec-WebSocket-Key header')
                return

            key = self.headers['Sec-WebSocket-Key']
            accept = self.calculate_websocket_accept(key)

            self.send_response(101, 'Switching Protocols')
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', accept)
            self.end_headers()

            # 获取原始socket
            client_socket = self.request
            
            async def proxy_websocket():
                async with websockets.connect(internal_ws_url) as ws:
                    print("成功连接到内部 WebSocket 服务器")
                    
                    async def forward_to_client():
                        try:
                            while True:
                                message = await ws.recv()
                                frame = self.create_websocket_frame(message)
                                client_socket.sendall(frame)
                        except Exception as e:
                            print(f"转发到客户端出错: {str(e)}")

                    async def forward_to_server():
                        try:
                            while True:
                                data = await asyncio.get_event_loop().run_in_executor(
                                    None, client_socket.recv, 4096
                                )
                                if not data:
                                    break
                                
                                # 解析WebSocket帧
                                payload = self.parse_websocket_frame(data)
                                if payload:
                                    await ws.send(payload)
                        except Exception as e:
                            print(f"转发到服务器出错: {str(e)}")

                    # 同时运行两个协程
                    await asyncio.gather(
                        forward_to_client(),
                        forward_to_server(),
                        return_exceptions=True
                    )

            # 在新的事件循环中运行异步代码
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                new_loop.run_until_complete(proxy_websocket())
            finally:
                new_loop.close()

        except Exception as e:
            print(f"WebSocket 处理错误: {str(e)}")
            self.send_error(502, f'WebSocket 代理错误: {str(e)}')

    def create_websocket_frame(self, data):
        """创建WebSocket帧"""
        if isinstance(data, str):
            payload = data.encode('utf-8')
            opcode = 0x1  # 文本帧
        else:
            payload = data
            opcode = 0x2  # 二进制帧

        payload_length = len(payload)
        if payload_length <= 125:
            header = struct.pack('!BB', 0x80 | opcode, payload_length)
        elif payload_length <= 65535:
            header = struct.pack('!BBH', 0x80 | opcode, 126, payload_length)
        else:
            header = struct.pack('!BBQ', 0x80 | opcode, 127, payload_length)

        return header + payload

    def parse_websocket_frame(self, data):
        """解析WebSocket帧"""
        if not data:
            return None

        byte1, byte2 = struct.unpack('!BB', data[:2])
        fin = (byte1 & 0x80) >> 7
        opcode = byte1 & 0x0F
        mask = (byte2 & 0x80) >> 7
        payload_length = byte2 & 0x7F

        # 计算头部长度
        header_length = 2
        if payload_length == 126:
            header_length += 2
            payload_length = struct.unpack('!H', data[2:4])[0]
        elif payload_length == 127:
            header_length += 8
            payload_length = struct.unpack('!Q', data[2:10])[0]

        if mask:
            mask_key = data[header_length:header_length + 4]
            header_length += 4

        # 获取负载数据
        payload = data[header_length:header_length + payload_length]

        # 如果有掩码，解码数据
        if mask:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return payload

    def calculate_websocket_accept(self, key):
        GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        sha1 = hashlib.sha1((key + GUID).encode()).digest()
        return base64.b64encode(sha1).decode()

    def proxy_request(self, method):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None
            
            # 使用实例变量 internal_port
            internal_conn = http.client.HTTPConnection('127.0.0.1', self.internal_port)
            headers = dict(self.headers)
            
            internal_conn.request(method, self.path, body=body, headers=headers)
            response = internal_conn.getresponse()
            
            self.send_response(response.status)
            for header, value in response.getheaders():
                self.send_header(header, value)
            self.end_headers()
            
            self.wfile.write(response.read())
            internal_conn.close()
            
        except Exception as e:
            self.send_error(502, f'代理错误: {str(e)}')
    
    def log_message(self, format, *args):
        pass

class ServerGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("局域网端口转发工具")
        self.root.geometry("800x600")  # 加窗口尺寸
        
        # 获取资源文件的路径
        def resource_path(relative_path):
            try:
                # PyInstaller创建临时文件夹,将路径存储在_MEIPASS中
                base_path = sys._MEIPASS
            except Exception:
                base_path = os.path.abspath(".")
            return os.path.join(base_path, relative_path)
        
        # 设置窗口图标
        try:
            icon_path = resource_path("Resources/logo.ico")
            self.root.iconbitmap(icon_path)
        except Exception as e:
            print(f"加载图标失败: {str(e)}")
        
        # 加载logo图标
        try:
            self.logo1 = self.load_icon(resource_path("Resources/logo1.ico"), (32, 32))
            self.logo2 = self.load_icon(resource_path("Resources/logo2.ico"), (32, 32))
        except Exception as e:
            print(f"加载Logo失败: {str(e)}")
            self.logo1 = None
            self.logo2 = None
        
        # 服务器状态
        self.server = None
        self.server_thread = None
        self.is_running = False
        
        # 端口映射列表
        self.port_mappings = []
        
        # 创建主框架
        self.create_widgets()
        
        # 开始处理消息队列
        self.root.after(100, self.process_queue)

    def load_icon(self, path, size):
        """加载并调整图标大小"""
        if os.path.exists(path):
            img = Image.open(path)
            img = img.resize(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        return None

    def create_widgets(self):
        # Logo框架
        logo_frame = ttk.Frame(self.root)
        logo_frame.pack(fill="x", padx=5, pady=5)

        # 添加Logo1
        if self.logo1:
            logo1_label = ttk.Label(logo_frame, image=self.logo1, cursor="hand2")
            logo1_label.pack(side="left", padx=5)
            logo1_label.bind('<Button-1>', lambda e: webbrowser.open('https://space.bilibili.com/1054925384?spm_id_from=333.1007.0.0'))

        # 添加Logo2
        if self.logo2:
            logo2_label = ttk.Label(logo_frame, image=self.logo2, cursor="hand2")
            logo2_label.pack(side="left", padx=5)
            logo2_label.bind('<Button-1>', lambda e: webbrowser.open('https://www.xiaohongshu.com/user/profile/59f1fcc411be101aba7f048f'))

        # 添加作者信息
        author_label = ttk.Label(
            logo_frame, 
            text="本软件开源免费，开源作者：猫咪老师", 
            foreground="gray"
        )
        author_label.pack(side="right", padx=10)

        # IP地址显示框架
        ip_frame = ttk.LabelFrame(self.root, text="网络信息", padding="5")
        ip_frame.pack(fill="x", padx=5, pady=5)

        # 显示本机IP
        local_ip = get_local_ip()
        ttk.Label(ip_frame, text="本机IP:").grid(row=0, column=0, padx=5, pady=5)
        ip_label = ttk.Label(ip_frame, text=local_ip, foreground="blue")
        ip_label.grid(row=0, column=1, padx=5, pady=5)
        
        # 复制IP按钮
        copy_button = ttk.Button(ip_frame, text="复制IP", 
                                command=lambda: self.copy_to_clipboard(local_ip))
        copy_button.grid(row=0, column=2, padx=5, pady=5)

        # 端口映射管理框架
        mapping_frame = ttk.LabelFrame(self.root, text="端口映射管理", padding="5")
        mapping_frame.pack(fill="x", padx=5, pady=5)

        # 加端口映射的输框
        ttk.Label(mapping_frame, text="外部端口:").grid(row=0, column=0, padx=5, pady=5)
        self.external_port = ttk.Entry(mapping_frame, width=10)
        self.external_port.grid(row=0, column=1, padx=5, pady=5)
        self.external_port.insert(0, "8000")

        ttk.Label(mapping_frame, text="内部端口:").grid(row=0, column=2, padx=5, pady=5)
        self.internal_port = ttk.Entry(mapping_frame, width=10)
        self.internal_port.grid(row=0, column=3, padx=5, pady=5)
        self.internal_port.insert(0, "8188")

        ttk.Label(mapping_frame, text="描述:").grid(row=0, column=4, padx=5, pady=5)
        self.mapping_desc = ttk.Entry(mapping_frame, width=20)
        self.mapping_desc.grid(row=0, column=5, padx=5, pady=5)
        self.mapping_desc.insert(0, "ComfyUI")

        # 添加映射按钮
        add_button = ttk.Button(mapping_frame, text="添加映射", 
                               command=self.add_port_mapping)
        add_button.grid(row=0, column=6, padx=5, pady=5)

        # 端口映射列表
        list_frame = ttk.LabelFrame(self.root, text="当前映射列表", padding="5")
        list_frame.pack(fill="x", padx=5, pady=5)

        # 创建表格
        columns = ('外部端口', '内部端口', '描述', '状态')
        self.mapping_tree = ttk.Treeview(list_frame, columns=columns, show='headings')
        
        # 设置列标题
        for col in columns:
            self.mapping_tree.heading(col, text=col)
            self.mapping_tree.column(col, width=100)

        self.mapping_tree.pack(fill="x", padx=5, pady=5)

        # 删除映射按钮
        delete_button = ttk.Button(list_frame, text="删除选中", 
                                 command=self.delete_port_mapping)
        delete_button.pack(side="left", padx=5, pady=5)

        # 控制按钮
        control_frame = ttk.Frame(self.root)
        control_frame.pack(fill="x", padx=5, pady=5)

        self.start_button = ttk.Button(control_frame, text="启动服务器", 
                                     command=self.start_server)
        self.start_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(control_frame, text="停止服务器", 
                                    command=self.stop_server, state="disabled")
        self.stop_button.pack(side="left", padx=5)

        # 日志显示
        log_frame = ttk.LabelFrame(self.root, text="服务器日志", padding="5")
        log_frame.pack(fill="both", expand=True, padx=5, pady=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=10)
        self.log_text.pack(fill="both", expand=True)

        # 状态栏
        self.status_var = tk.StringVar()
        self.status_var.set("就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief="sunken")
        status_bar.pack(fill="x", side="bottom", padx=5, pady=5)

    def add_port_mapping(self):
        try:
            external = int(self.external_port.get())
            internal = int(self.internal_port.get())
            desc = self.mapping_desc.get()
            
            # 检查端口是否已存在
            for item in self.mapping_tree.get_children():
                if self.mapping_tree.item(item)['values'][0] == external:
                    self.log_message(f"错误：外部端口 {external} 已存在")
                    return
            
            # 添加到列表
            self.mapping_tree.insert('', 'end', values=(external, internal, desc, '未启动'))
            self.log_message(f"添加端口映射: {external} -> {internal} ({desc})")
            
            # 清空输入框
            self.external_port.delete(0, tk.END)
            self.internal_port.delete(0, tk.END)
            self.mapping_desc.delete(0, tk.END)
            
        except ValueError:
            self.log_message("错误：端口必须是数字")

    def delete_port_mapping(self):
        selected = self.mapping_tree.selection()
        if not selected:
            return
            
        for item in selected:
            values = self.mapping_tree.item(item)['values']
            self.mapping_tree.delete(item)
            self.log_message(f"删除端口映射: {values[0]} -> {values[1]} ({values[2]})")

    def start_server(self):
        try:
            mappings = []
            for item in self.mapping_tree.get_children():
                values = self.mapping_tree.item(item)['values']
                mappings.append({
                    'external': int(values[0]),
                    'internal': int(values[1]),
                    'desc': values[2]
                })
            
            if not mappings:
                self.log_message("错误：没有端口映射")
                return
                
            # 检查所有外部端口是否可用
            for mapping in mappings:
                if not check_port_available(mapping['external']):
                    self.log_message(f"错误：端口 {mapping['external']} 已被占用")
                    return

            # 启动所有服务器
            self.servers = []
            for mapping in mappings:
                server = ThreadedHTTPServer(
                    ('0.0.0.0', mapping['external']), 
                    lambda *args: ProxyHandler(*args, internal_port=mapping['internal'])
                )
                thread = threading.Thread(target=server.serve_forever)
                thread.daemon = True
                thread.start()
                self.servers.append((server, thread))
                
                # 更新状态
                for item in self.mapping_tree.get_children():
                    if self.mapping_tree.item(item)['values'][0] == mapping['external']:
                        self.mapping_tree.item(item, values=(
                            mapping['external'],
                            mapping['internal'],
                            mapping['desc'],
                            '运行中'
                        ))

            local_ip = get_local_ip()
            self.log_message("所有服务器已启动:")
            for mapping in mappings:
                self.log_message(
                    f"{mapping['desc']}: "
                    f"http://{local_ip}:{mapping['external']} -> "
                    f"localhost:{mapping['internal']}"
                )
            
            self.status_var.set("运行中")
            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")
            self.is_running = True

        except Exception as e:
            self.log_message(f"启动服务器时发生错误: {str(e)}")

    def stop_server(self):
        if hasattr(self, 'servers'):
            for server, thread in self.servers:
                server.stop()
            self.servers = []
            
            # 更新所有映射状态
            for item in self.mapping_tree.get_children():
                values = self.mapping_tree.item(item)['values']
                self.mapping_tree.item(item, values=(
                    values[0], values[1], values[2], '已停止'
                ))
            
            self.is_running = False
            self.status_var.set("已停止")
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.log_message("所有服务器已停止")

    def on_closing(self):
        if self.is_running:
            self.stop_server()
        self.root.destroy()

    def copy_to_clipboard(self, text):
        """复制文本到剪贴板"""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log_message("IP地址已复制到剪贴板")

    def log_message(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")

    def process_queue(self):
        while not message_queue.empty():
            message = message_queue.get()
            if message.strip():  # 只显示非空消息
                self.log_message(message)
        self.root.after(100, self.process_queue)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def check_port_available(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('', port))
        sock.close()
        return True
    except OSError:
        return False

def main():
    try:
        # 首先安装必要的依赖
        import subprocess
        import sys
        
        try:
            import websockets
            from PIL import Image
        except ImportError:
            print("正在安装必要的依赖...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", 
                "websocket-client", "websockets", "pillow"])
            print("依赖安装完成")

        # 创建GUI窗口
        root = tk.Tk()
        app = ServerGUI(root)
        
        # 重定向标准输出到消息队列
        sys.stdout = RedirectText(message_queue)
        
        # 设置关闭窗口的处理
        root.protocol("WM_DELETE_WINDOW", app.on_closing)
        
        # 运行GUI主循环
        root.mainloop()
        
    except KeyboardInterrupt:
        print('\n服务器已停止')
    except Exception as e:
        print(f"发生错误: {str(e)}")

if __name__ == '__main__':
    main()