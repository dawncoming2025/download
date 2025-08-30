import os
import sys
import socket
import threading
import time
import datetime
import json
import pickle
import uuid
import platform
from tkinter import *
from tkinter import ttk, messagebox, filedialog, scrolledtext
from PIL import Image, ImageTk, ImageDraw, ImageOps, ImageFont
import base64
import io

# 全局配置
BROADCAST_PORT = 12345
TCP_PORT = 54321
BUFFER_SIZE = 4096
HEARTBEAT_INTERVAL = 5  # 心跳间隔(秒)
DISCOVERY_INTERVAL = 10  # 设备发现间隔(秒)
OFFLINE_TIMEOUT = 30  # 设备超时时间(秒)
MAX_RECORD_FILE_SIZE = 512 * 1024 * 1024  # 512MB
DEFAULT_AVATAR_SIZE = 100  # 默认头像尺寸

class NetworkDevice:
    def __init__(self, ip, mac, name, avatar=None, timestamp=None):
        self.ip = ip
        self.mac = mac
        self.name = name
        self.avatar = avatar or self.generate_default_avatar(name)
        self.timestamp = timestamp or time.time()
        self.is_online = True
        self.unread_messages = 0
        
    def update_timestamp(self):
        self.timestamp = time.time()
        
    def check_online(self):
        self.is_online = (time.time() - self.timestamp) < OFFLINE_TIMEOUT
        return self.is_online
        
    def __str__(self):
        return f"{self.name} ({self.ip})"
    
    def to_dict(self):
        """只包含基本信息，不包含完整头像，解决广播包过大问题"""
        return {
            "ip": self.ip,
            "mac": self.mac,
            "name": self.name,
            "timestamp": self.timestamp
        }
    
    def avatar_base64(self):
        if isinstance(self.avatar, bytes):
            return base64.b64encode(self.avatar).decode('utf-8')
        return self.avatar
    
    def generate_default_avatar(self, name):
        """使用Data\Image\目录下的headimg1~5.png作为默认头像，如果不存在则生成"""
        default_avatars = []
        # 检查是否存在自定义默认头像
        img_dir = os.path.join("Data", "Image")
        for i in range(1, 6):
            img_path = os.path.join(img_dir, f"headimg{i}.png")
            if os.path.exists(img_path):
                try:
                    with open(img_path, 'rb') as f:
                        default_avatars.append(f.read())
                except:
                    continue
        
        # 如果有可用的默认头像
        if default_avatars:
            return default_avatars[hash(name) % len(default_avatars)]
        
        # 如果没有自定义头像，生成默认头像
        colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#FFA07A', '#98D8C8']
        color = colors[hash(name) % len(colors)]
        
        img = Image.new('RGBA', (DEFAULT_AVATAR_SIZE, DEFAULT_AVATAR_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((0, 0, DEFAULT_AVATAR_SIZE-1, DEFAULT_AVATAR_SIZE-1), fill=color)
        
        if name:
            letter = name[0].upper()
            font = ImageFont.load_default()
            font = font.font_variant(size=int(DEFAULT_AVATAR_SIZE/2))
            draw.text((DEFAULT_AVATAR_SIZE/2, DEFAULT_AVATAR_SIZE/2), 
                      letter, fill="white", font=font, anchor="mm")
        
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        return img_byte_arr.getvalue()
    
    @staticmethod
    def create_avatar_from_base64(avatar_str):
        if avatar_str:
            try:
                return base64.b64decode(avatar_str.encode('utf-8'))
            except:
                pass
        return None
    
    def get_safe_mac(self):
        """获取安全的MAC地址，用于文件名"""
        return self.mac.replace(':', '_')

class LanChatApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DCChatting | 局域网通信工具")
        self.root.geometry("1000x700")
        self.root.resizable(True, True)
        
        # 设置窗口图标
        self.set_window_icon()
        
        # 创建数据目录和图片目录
        self.data_dir = "Data"
        self.image_dir = os.path.join(self.data_dir, "Image")
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.image_dir, exist_ok=True)
        
        # 线程锁，确保共享数据安全
        self.devices_lock = threading.Lock()
        self.connections_lock = threading.Lock()
        
        # 获取本地设备信息
        self.local_device = self.get_local_device()
        self.devices = {}  # MAC -> NetworkDevice
        self.connections = {}  # IP -> socket
        self.active_chats = {}  # device_name -> chat_info
        
        # 当前选中的聊天设备
        self.current_chat_device = None
        
        # 创建主界面
        self.create_main_interface()
        
        # 启动网络服务
        self.running = True
        self.start_networking()
        
        # 启动设备发现线程
        threading.Thread(target=self.discovery_loop, daemon=True).start()
        
        # 启动心跳线程
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        
        # 启动超时检查线程
        threading.Thread(target=self.timeout_check_loop, daemon=True).start()
        
        # 关闭窗口时的清理
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def set_window_icon(self):
        """设置窗口图标为当前目录下的icon.ico"""
        try:
            if os.path.exists("icon.ico"):
                self.root.iconbitmap("icon.ico")
        except Exception as e:
            print(f"设置图标失败: {e}")
    
    def get_local_ip(self):
        """获取非本地的IP地址"""
        try:
            # 尝试连接到外部服务器获取IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except:
            pass
            
        # 如果连接外部失败，遍历所有网络接口
        for interface in socket.if_nameindex():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((interface[1], 0))
                sock.connect(("8.8.8.8", 80))
                ip = sock.getsockname()[0]
                sock.close()
                if not ip.startswith("127."):
                    return ip
            except:
                continue
                
        return "127.0.0.1"
    
    def get_local_device(self):
        # 获取本机IP地址
        ip = self.get_local_ip()
        
        # 获取MAC地址
        mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff) 
                       for elements in range(0,2*6,2)][::-1])
        
        # 获取计算机名作为默认名称
        name = platform.node()
        
        # 尝试从配置文件加载用户设置的名称
        name_config_path = os.path.join(self.data_dir, "username.txt")
        if os.path.exists(name_config_path):
            try:
                with open(name_config_path, 'r', encoding='utf-8') as f:
                    saved_name = f.read().strip()
                    if saved_name:
                        name = saved_name
            except:
                pass
        
        # 创建默认头像
        avatar = None
        
        # 尝试加载自定义头像
        avatar_path = os.path.join(self.data_dir, "avatar.png")
        if os.path.exists(avatar_path):
            try:
                img = Image.open(avatar_path)
                img = self.crop_to_circle(img)
                img_byte_arr = io.BytesIO()
                img.save(img_byte_arr, format='PNG')
                avatar = img_byte_arr.getvalue()
            except:
                pass
        
        return NetworkDevice(ip, mac, name, avatar)
    
    def save_username(self, username):
        """保存用户名到配置文件"""
        try:
            name_config_path = os.path.join(self.data_dir, "username.txt")
            with open(name_config_path, 'w', encoding='utf-8') as f:
                f.write(username)
            return True
        except Exception as e:
            print(f"保存用户名失败: {e}")
            return False
    
    def change_username(self):
        """修改用户名对话框"""
        def apply_change():
            new_name = entry.get().strip()
            if new_name and new_name != self.local_device.name:
                old_name = self.local_device.name
                self.local_device.name = new_name
                # 保存到配置文件
                self.save_username(new_name)
                # 更新UI显示
                self.local_name_label.config(text=new_name)
                # 广播名称更新
                self.broadcast_name_change(old_name, new_name)
                top.destroy()
        
        top = Toplevel(self.root)
        top.title("修改用户名")
        top.geometry("300x150")
        top.resizable(False, False)
        top.transient(self.root)
        top.grab_set()
        
        frame = ttk.Frame(top, padding=20)
        frame.pack(fill=BOTH, expand=True)
        
        ttk.Label(frame, text="请输入新的用户名:").pack(anchor=W, pady=5)
        
        entry = ttk.Entry(frame)
        entry.pack(fill=X, pady=10)
        entry.insert(0, self.local_device.name)
        entry.focus()
        
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=X)
        
        ttk.Button(btn_frame, text="取消", command=top.destroy).pack(side=RIGHT, padx=5)
        ttk.Button(btn_frame, text="确定", command=apply_change).pack(side=RIGHT)
        
        # 按Enter键确认
        entry.bind("<Return>", lambda e: apply_change())
    
    def broadcast_name_change(self, old_name, new_name):
        """广播名称变更信息给所有在线计算机"""
        # 立即广播设备信息
        self.broadcast_device_info()
        
        # 向所有已连接设备发送名称变更消息
        name_change_msg = pickle.dumps({
            'type': 'name_change',
            'old_name': old_name,
            'new_name': new_name,
            'mac': self.local_device.mac
        })
        
        with self.connections_lock:
            for sock in list(self.connections.values()):
                try:
                    sock.send(name_change_msg)
                except Exception as e:
                    print(f"发送名称变更消息失败: {e}")
        
        # 更新本地设备列表中的名称
        with self.devices_lock:
            for device in self.devices.values():
                if device.name == old_name:
                    device.name = new_name
                    break
        
        # 更新设备列表显示
        self.update_devices_listbox()
        
        # 如果当前正在与改名设备聊天，更新聊天窗口
        if self.current_chat_device and self.current_chat_device.name == old_name:
            self.current_chat_device.name = new_name
            self.create_chat_interface(self.current_chat_device)
    
    def crop_to_circle(self, img):
        """将图像裁剪为圆形"""
        size = min(img.size)
        mask = Image.new('L', (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)
        
        output = ImageOps.fit(img, (size, size), centering=(0.5, 0.5))
        output.putalpha(mask)
        return output
    
    def create_main_interface(self):
        # 创建主框架
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=BOTH, expand=True, padx=10, pady=10)
        
        # 分割左右面板
        paned_window = ttk.PanedWindow(main_frame, orient=HORIZONTAL)
        paned_window.pack(fill=BOTH, expand=True)
        
        # 左侧设备面板
        left_frame = ttk.Frame(paned_window, width=250)
        paned_window.add(left_frame, weight=0)
        
        # 标题、头像和名称修改按钮
        self.title_frame = ttk.Frame(left_frame)
        self.title_frame.pack(fill=X, pady=(0, 10))
        
        # 显示本地头像
        self.local_avatar_img = self.create_avatar_image(self.local_device.avatar, 60)
        avatar_label = ttk.Label(self.title_frame, image=self.local_avatar_img)
        avatar_label.image = self.local_avatar_img
        avatar_label.pack(side=LEFT, padx=(0, 10))
        
        # 名称和修改按钮
        name_frame = ttk.Frame(self.title_frame)
        name_frame.pack(side=LEFT, fill=X, expand=True)
        
        # 设备名称 - 使用单独的变量引用以便更新
        self.local_name_label = ttk.Label(name_frame, text=self.local_device.name, 
                 font=("Arial", 12, "bold"))
        self.local_name_label.pack(anchor=W)
        
        # 修改名称按钮
        ttk.Button(name_frame, text="修改名称", width=8, 
                  command=self.change_username).pack(anchor=W)
        
        # 更改头像按钮
        ttk.Button(self.title_frame, text="更改头像", width=10, 
                  command=self.change_avatar).pack(side=RIGHT, padx=(10, 0))
        
        # 搜索框
        search_frame = ttk.Frame(left_frame)
        search_frame.pack(fill=X, pady=(0, 10))
        
        self.search_var = StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        search_entry.bind("<KeyRelease>", self.filter_devices)
        
        ttk.Button(search_frame, text="搜索", 
                  command=self.filter_devices).pack(side=LEFT)
        
        # 在线设备列表
        devices_frame = ttk.LabelFrame(left_frame, text="在线设备")
        devices_frame.pack(fill=BOTH, expand=True)
        
        # 创建Treeview
        self.devices_tree = ttk.Treeview(devices_frame, columns=["name"], show="headings")
        self.devices_tree.column("name", width=180)
        self.devices_tree.heading("name", text="设备名称")
        
        scrollbar = ttk.Scrollbar(devices_frame, orient=VERTICAL, 
                                 command=self.devices_tree.yview)
        self.devices_tree.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=RIGHT, fill=Y)
        self.devices_tree.pack(fill=BOTH, expand=True, padx=0, pady=0)
        
        # 绑定选择事件
        self.devices_tree.bind('<<TreeviewSelect>>', self.on_device_select)
        
        # 右侧聊天面板
        self.right_frame = ttk.Frame(paned_window)
        paned_window.add(self.right_frame, weight=1)
        
        # 创建初始聊天界面
        self.create_chat_placeholder()
        
        # 状态栏
        self.status_var = StringVar()
        self.status_var.set("就绪")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=SUNKEN, anchor=W)
        status_bar.pack(side=BOTTOM, fill=X)
    
    def create_chat_placeholder(self):
        # 清除右侧面板
        for widget in self.right_frame.winfo_children():
            widget.destroy()
        
        # 创建占位界面
        placeholder_frame = ttk.Frame(self.right_frame)
        placeholder_frame.pack(fill=BOTH, expand=True, padx=50, pady=50)
        
        # 添加提示信息
        ttk.Label(placeholder_frame, text="局域网通信工具", 
                 font=("Arial", 24, "bold"), foreground="#4e8cff").pack(pady=20)
        
        # 添加图标
        try:
            img = Image.open("chat_icon.png")
        except:
            # 创建默认图标
            img = Image.new('RGB', (256, 256), color='white')
            draw = ImageDraw.Draw(img)
            draw.ellipse((50, 50, 206, 206), outline='#4e8cff', width=10)
            draw.line((80, 180, 176, 180), fill='#4e8cff', width=10)
        
        img = img.resize((200, 200), Image.LANCZOS)
        self.chat_img = ImageTk.PhotoImage(img)
        
        ttk.Label(placeholder_frame, image=self.chat_img).pack(pady=20)
        
        info_text = "请从左侧设备列表中选择一个设备开始聊天\n\n"
        info_text += "功能说明:\n"
        info_text += "- 搜索框可以按名称过滤设备\n"
        info_text += "- 点击设备可以开始聊天\n"
        info_text += "- 支持文本消息和文件传输\n"
        info_text += "- 聊天记录自动保存并分页存储\n"
        info_text += "- 可以更改个人头像和用户名"
        
        ttk.Label(placeholder_frame, text=info_text, 
                 font=("Arial", 11), justify=LEFT).pack(pady=10)
    
    def create_chat_interface(self, device):
        # 清除右侧面板
        for widget in self.right_frame.winfo_children():
            widget.destroy()
        
        # 创建聊天主框架
        chat_frame = ttk.Frame(self.right_frame)
        chat_frame.pack(fill=BOTH, expand=True, padx=10, pady=10)
        
        # 聊天头部 (显示对方信息)
        header_frame = ttk.Frame(chat_frame)
        header_frame.pack(fill=X, pady=(0, 10))
        
        # 显示对方头像
        avatar_img = self.create_avatar_image(device.avatar, 50)
        avatar_label = ttk.Label(header_frame, image=avatar_img)
        avatar_label.image = avatar_img
        avatar_label.pack(side=LEFT, padx=(0, 10))
        
        # 对方信息
        info_frame = ttk.Frame(header_frame)
        info_frame.pack(side=LEFT, fill=X, expand=True)
        
        # 对方名称标签 - 使用变量以便后续更新
        self.current_chat_name_label = ttk.Label(info_frame, text=device.name, 
                 font=("Arial", 14, "bold"))
        self.current_chat_name_label.pack(anchor=W)
        
        status_text = "在线" if device.is_online else "离线"
        status_color = "green" if device.is_online else "gray"
        ttk.Label(info_frame, text=status_text, 
                 font=("Arial", 10), foreground=status_color).pack(anchor=W)
        
        # 聊天记录区域
        chat_history_frame = ttk.LabelFrame(chat_frame, text="聊天记录")
        chat_history_frame.pack(fill=BOTH, expand=True, pady=(0, 10))
        
        chat_text = scrolledtext.ScrolledText(chat_history_frame, wrap=WORD, state="disabled")
        chat_text.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        # 输入区域
        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill=X, pady=(0, 10))
        
        input_entry = ttk.Entry(input_frame)
        input_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        input_entry.bind("<Return>", lambda e: self.send_message(device, input_entry))
        
        send_btn = ttk.Button(input_frame, text="发送", 
                             command=lambda: self.send_message(device, input_entry))
        send_btn.pack(side=LEFT)
        
        # 文件发送按钮
        file_btn = ttk.Button(input_frame, text="发送文件", 
                             command=lambda: self.send_file(device))
        file_btn.pack(side=LEFT, padx=(5, 0))
        
        # 管理记录按钮
        manage_btn = ttk.Button(input_frame, text="管理记录", 
                              command=self.manage_records)
        manage_btn.pack(side=RIGHT, padx=(5, 0))
        
        # 保存聊天界面引用
        self.active_chats[device.name] = {
            "text_widget": chat_text,
            "input_entry": input_entry,
            "device": device,
            "name_label": self.current_chat_name_label
        }
        
        # 加载历史消息
        self.load_history_messages(device, chat_text)
        
        # 重置未读消息计数
        with self.devices_lock:
            device.unread_messages = 0
        self.update_devices_listbox()
    
    def create_avatar_image(self, avatar_data, size):
        """从字节数据创建头像图像"""
        try:
            if avatar_data:
                img = Image.open(io.BytesIO(avatar_data))
            else:
                # 创建默认头像
                img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.ellipse((0, 0, size-1, size-1), fill="#4e8cff")
        except:
            # 创建默认头像
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((0, 0, size-1, size-1), fill="#4e8cff")
        
        # 调整大小并转换为圆形
        img = img.resize((size, size), Image.LANCZOS)
        img = self.crop_to_circle(img)
        return ImageTk.PhotoImage(img)
    
    def start_networking(self):
        # 启动UDP广播监听
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp_socket.bind(('0.0.0.0', BROADCAST_PORT))
        
        # 启动TCP服务器
        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp_socket.bind(('0.0.0.0', TCP_PORT))
        self.tcp_socket.listen(5)
        
        # 启动线程处理网络通信
        threading.Thread(target=self.udp_listener, daemon=True).start()
        threading.Thread(target=self.tcp_listener, daemon=True).start()
    
    def udp_listener(self):
        while self.running:
            try:
                data, addr = self.udp_socket.recvfrom(BUFFER_SIZE)
                device_info = json.loads(data.decode('utf-8'))
                
                # 忽略自己的广播
                if device_info['mac'] == self.local_device.mac:
                    continue
                
                # 更新设备列表
                with self.devices_lock:
                    # 检查设备是否已存在
                    if device_info['mac'] in self.devices:
                        # 只更新时间戳和名称
                        self.devices[device_info['mac']].timestamp = time.time()
                        self.devices[device_info['mac']].name = device_info['name']
                        self.devices[device_info['mac']].ip = device_info['ip']
                    else:
                        # 创建新设备
                        device = NetworkDevice(
                            device_info['ip'],
                            device_info['mac'],
                            device_info['name'],
                            timestamp=time.time()
                        )
                        self.devices[device.mac] = device
                
                self.update_devices_listbox()
                
            except Exception as e:
                if self.running:
                    print(f"UDP监听错误: {e}")
    
    def tcp_listener(self):
        while self.running:
            try:
                client_socket, addr = self.tcp_socket.accept()
                threading.Thread(target=self.handle_tcp_connection, args=(client_socket, addr), daemon=True).start()
            except Exception as e:
                if self.running:
                    print(f"TCP监听错误: {e}")
    
    def handle_tcp_connection(self, client_socket, addr):
        device = None
        try:
            # 接收设备信息
            data = client_socket.recv(BUFFER_SIZE)
            if not data:
                return
                
            device_info = json.loads(data.decode('utf-8'))
            mac = device_info['mac']
            
            # 检查是否已知设备
            with self.devices_lock:
                if mac in self.devices:
                    device = self.devices[mac]
                else:
                    device = NetworkDevice(addr[0], mac, device_info['name'])
                    self.devices[mac] = device
            
            # 发送本地头像
            client_socket.send(pickle.dumps({
                'type': 'avatar',
                'content': self.local_device.avatar_base64()
            }))
            
            # 接收对方头像
            try:
                avatar_data = client_socket.recv(BUFFER_SIZE)
                if avatar_data:
                    avatar_msg = pickle.loads(avatar_data)
                    if avatar_msg['type'] == 'avatar':
                        with self.devices_lock:
                            device.avatar = NetworkDevice.create_avatar_from_base64(avatar_msg['content'])
            except:
                pass
            
            # 更新设备列表
            self.update_devices_listbox()
            
            # 保存连接
            with self.connections_lock:
                self.connections[device.ip] = client_socket
            
            # 打开聊天窗口
            if device.name in self.active_chats and self.current_chat_device == device:
                self.create_chat_interface(device)
            elif not self.current_chat_device:
                self.create_chat_interface(device)
                self.current_chat_device = device
            
            # 持续接收数据
            while self.running:
                try:
                    data = client_socket.recv(BUFFER_SIZE)
                    if not data:
                        break
                        
                    # 尝试解析消息
                    try:
                        message = pickle.loads(data)
                        if message['type'] == 'text':
                            self.receive_message(device, message['content'])
                        elif message['type'] == 'file_metadata':
                            # 处理文件传输
                            self.handle_file_reception(client_socket, device, message)
                        elif message['type'] == 'name_change':
                            # 处理名称变更
                            self.handle_name_change(message['old_name'], message['new_name'], message['mac'])
                        elif message['type'] == 'avatar_update':
                            # 处理头像更新
                            with self.devices_lock:
                                if message['mac'] in self.devices:
                                    self.devices[message['mac']].avatar = NetworkDevice.create_avatar_from_base64(message['content'])
                            self.update_devices_listbox()
                            if self.current_chat_device and self.current_chat_device.mac == message['mac']:
                                self.create_chat_interface(self.current_chat_device)
                    except Exception as e:
                        print(f"解析消息错误: {e}")
                except Exception as e:
                    print(f"接收数据错误: {e}")
                    break
                
        except Exception as e:
            print(f"TCP连接处理错误: {e}")
        finally:
            if device:
                with self.connections_lock:
                    if device.ip in self.connections:
                        del self.connections[device.ip]
            try:
                client_socket.close()
            except:
                pass
    
    def handle_name_change(self, old_name, new_name, mac):
        """处理接收到的名称变更消息"""
        with self.devices_lock:
            if mac in self.devices:
                device = self.devices[mac]
                device.name = new_name
        
        # 更新设备列表显示
        self.update_devices_listbox()
        
        # 更新聊天窗口引用
        if old_name in self.active_chats:
            chat_info = self.active_chats.pop(old_name)
            self.active_chats[new_name] = chat_info
            self.active_chats[new_name]['device'].name = new_name
            
            # 更新聊天窗口中的名称标签
            if 'name_label' in chat_info:
                chat_info['name_label'].config(text=new_name)
            else:
                # 如果找不到标签引用，重新创建聊天界面
                if self.current_chat_device and self.current_chat_device.mac == mac:
                    self.create_chat_interface(self.current_chat_device)
        
        # 如果正在聊天，刷新聊天窗口
        if self.current_chat_device and self.current_chat_device.mac == mac:
            self.current_chat_device.name = new_name
            self.create_chat_interface(self.current_chat_device)
    
    def broadcast_device_info(self):
        """立即广播设备信息"""
        device_info = {
            'ip': self.local_device.ip,
            'mac': self.local_device.mac,
            'name': self.local_device.name,
            'timestamp': self.local_device.timestamp
        }
        
        try:
            self.udp_socket.sendto(
                json.dumps(device_info).encode('utf-8'),
                ('<broadcast>', BROADCAST_PORT)
            )
        except Exception as e:
            print(f"广播错误: {e}")
    
    def discovery_loop(self):
        while self.running:
            # 广播设备信息
            self.broadcast_device_info()
            time.sleep(DISCOVERY_INTERVAL)
    
    def heartbeat_loop(self):
        while self.running:
            # 向所有连接的设备发送心跳
            with self.connections_lock:
                for ip, sock in list(self.connections.items()):
                    try:
                        sock.send(b'HEARTBEAT')
                    except:
                        if ip in self.connections:
                            del self.connections[ip]
            
            time.sleep(HEARTBEAT_INTERVAL)
    
    def timeout_check_loop(self):
        while self.running:
            # 检查设备超时
            offline_devices = []
            with self.devices_lock:
                for mac, device in list(self.devices.items()):
                    if not device.check_online():
                        offline_devices.append(mac)
            
            with self.devices_lock:
                for mac in offline_devices:
                    if mac in self.devices:
                        del self.devices[mac]
            
            if offline_devices:
                self.update_devices_listbox()
            
            time.sleep(5)
    
    def update_devices_listbox(self):
        # 清空现有项
        for item in self.devices_tree.get_children():
            self.devices_tree.delete(item)
        
        # 获取搜索关键词
        search_term = self.search_var.get().lower()
        
        # 添加设备到列表
        with self.devices_lock:
            # 按名称排序设备
            sorted_devices = sorted(self.devices.values(), key=lambda d: d.name)
            
            for device in sorted_devices:
                # 过滤搜索结果
                if search_term and search_term not in device.name.lower():
                    continue
                
                # 创建显示文本（包含未读消息计数）
                display_text = device.name
                if device.unread_messages > 0:
                    display_text += f" ({device.unread_messages})"
                
                # 设置字体样式
                tags = []
                if device.unread_messages > 0:
                    self.devices_tree.tag_configure("unread", font=("Arial", 10, "bold"), foreground="blue")
                    tags.append("unread")
                elif not device.is_online:
                    self.devices_tree.tag_configure("offline", font=("Arial", 10), foreground="gray")
                    tags.append("offline")
                else:
                    self.devices_tree.tag_configure("online", font=("Arial", 10), foreground="black")
                    tags.append("online")
                
                # 添加到Treeview
                self.devices_tree.insert("", END, values=[display_text], tags=tags)
    
    def on_device_select(self, event):
        # 获取选中的设备
        selection = self.devices_tree.selection()
        if not selection:
            return
            
        item = selection[0]
        display_text = self.devices_tree.item(item, "values")[0]
        device_name = display_text.split(' (')[0]  # 移除未读计数
        
        # 找到对应的设备对象
        device = None
        with self.devices_lock:
            for dev in self.devices.values():
                if dev.name == device_name:
                    device = dev
                    break
        
        if not device:
            return
            
        # 尝试连接到设备（如果尚未连接）
        with self.connections_lock:
            if device.ip not in self.connections:
                try:
                    # 创建TCP连接
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(10)  # 设置连接超时
                    sock.connect((device.ip, TCP_PORT))
                    
                    # 发送本地设备信息
                    device_info = {
                        'ip': self.local_device.ip,
                        'mac': self.local_device.mac,
                        'name': self.local_device.name
                    }
                    sock.send(json.dumps(device_info).encode('utf-8'))
                    
                    # 发送头像
                    sock.send(pickle.dumps({
                        'type': 'avatar',
                        'content': self.local_device.avatar_base64()
                    }))
                    
                    # 接收对方头像
                    try:
                        avatar_data = sock.recv(BUFFER_SIZE)
                        if avatar_data:
                            avatar_msg = pickle.loads(avatar_data)
                            if avatar_msg['type'] == 'avatar':
                                with self.devices_lock:
                                    device.avatar = NetworkDevice.create_avatar_from_base64(avatar_msg['content'])
                    except:
                        pass
                    
                    # 保存连接
                    self.connections[device.ip] = sock
                    
                    # 启动线程处理这个连接
                    threading.Thread(target=self.handle_tcp_connection, args=(sock, (device.ip, TCP_PORT)), daemon=True).start()
                except Exception as e:
                    messagebox.showerror("连接失败", f"无法连接到设备: {e}")
                    return
        
        # 设置当前聊天设备
        self.current_chat_device = device
        
        # 创建聊天界面
        self.create_chat_interface(device)
    
    def send_message(self, device, input_entry):
        message = input_entry.get().strip()
        if not message:
            return
            
        input_entry.delete(0, END)
        
        # 保存消息到本地
        self.save_message(device, message, sent=True)
        
        # 更新聊天窗口
        if device.name in self.active_chats:
            chat = self.active_chats[device.name]
            self.append_message(chat["text_widget"], 
                              f"我 ({datetime.datetime.now().strftime('%H:%M:%S')}): {message}")
        
        # 发送消息
        with self.connections_lock:
            if device.ip in self.connections:
                try:
                    data = pickle.dumps({
                        'type': 'text',
                        'content': message
                    })
                    self.connections[device.ip].send(data)
                except Exception as e:
                    print(f"发送消息失败: {e}")
                    messagebox.showerror("发送失败", "无法发送消息")
    
    def receive_message(self, device, message):
        # 保存消息
        self.save_message(device, message, sent=False)
        
        # 更新聊天窗口
        if device.name in self.active_chats and self.current_chat_device == device:
            chat = self.active_chats[device.name]
            self.append_message(chat["text_widget"], 
                              f"{device.name} ({datetime.datetime.now().strftime('%H:%M:%S')}): {message}")
        else:
            # 增加未读消息计数
            with self.devices_lock:
                device.unread_messages += 1
            self.update_devices_listbox()
    
    def send_file(self, device):
        filepath = filedialog.askopenfilename(title="选择要发送的文件")
        if not filepath:
            return
            
        filename = os.path.basename(filepath)
        
        try:
            # 检查文件大小
            file_size = os.path.getsize(filepath)
            if file_size > MAX_RECORD_FILE_SIZE:
                messagebox.showerror("文件过大", f"文件大小不能超过{MAX_RECORD_FILE_SIZE//(1024*1024)}MB")
                return
                
            # 读取文件内容
            with open(filepath, 'rb') as f:
                file_data = f.read()
                
            # 保存文件到本地记录
            self.save_file(device, filename, file_data, sent=True)
            
            # 更新聊天窗口
            if device.name in self.active_chats:
                chat = self.active_chats[device.name]
                self.append_message(chat["text_widget"], 
                                  f"我 ({datetime.datetime.now().strftime('%H:%M:%S')}): 发送文件 '{filename}'")
            
            # 发送文件
            with self.connections_lock:
                if device.ip in self.connections:
                    try:
                        # 先发送文件元数据
                        metadata = pickle.dumps({
                            'type': 'file_metadata',
                            'filename': filename,
                            'size': len(file_data)
                        })
                        self.connections[device.ip].send(metadata)
                        
                        # 等待确认，增加超时和重试机制
                        ready_received = False
                        retries = 3
                        timeout = 5  # 5秒超时
                        
                        for attempt in range(retries):
                            try:
                                # 设置超时
                                self.connections[device.ip].settimeout(timeout)
                                response = self.connections[device.ip].recv(BUFFER_SIZE)
                                if response == b'READY':
                                    ready_received = True
                                    break
                            except socket.timeout:
                                print(f"等待READY超时，尝试 {attempt + 1}/{retries}")
                                # 重新发送元数据
                                self.connections[device.ip].send(metadata)
                            except Exception as e:
                                print(f"接收READY错误: {e}")
                                break
                        
                        # 恢复超时设置
                        self.connections[device.ip].settimeout(None)
                        
                        if not ready_received:
                            raise Exception("接收方未准备好或超时")
                            
                        # 分块发送文件数据
                        offset = 0
                        while offset < len(file_data):
                            chunk = file_data[offset:offset+BUFFER_SIZE]
                            self.connections[device.ip].send(pickle.dumps({
                                'type': 'file_chunk',
                                'content': chunk
                            }))
                            offset += BUFFER_SIZE
                            
                        # 发送结束标志
                        self.connections[device.ip].send(pickle.dumps({
                            'type': 'file_end'
                        }))
                        
                        # 更新聊天窗口，显示发送成功
                        if device.name in self.active_chats:
                            chat = self.active_chats[device.name]
                            self.append_message(chat["text_widget"], 
                                              f"文件 '{filename}' 发送成功")
                            
                    except Exception as e:
                        error_msg = f"无法发送文件: {e}"
                        print(error_msg)
                        messagebox.showerror("发送失败", error_msg)
                        
                        # 更新聊天窗口，显示发送失败
                        if device.name in self.active_chats:
                            chat = self.active_chats[device.name]
                            self.append_message(chat["text_widget"], 
                                              f"文件 '{filename}' 发送失败: {e}")
        except Exception as e:
            error_msg = f"读取文件失败: {e}"
            print(error_msg)
            messagebox.showerror("错误", error_msg)

    def handle_file_reception(self, sock, device, metadata):
        """处理文件接收"""
        try:
            filename = metadata['filename']
            file_size = metadata['size']
            
            # 立即发送准备就绪确认
            sock.send(b'READY')
            
            # 接收文件内容
            file_data = b''
            start_time = time.time()
            timeout = 30  # 30秒超时
            
            while len(file_data) < file_size:
                # 检查超时
                if time.time() - start_time > timeout:
                    raise Exception("文件接收超时")
                    
                chunk_data = sock.recv(BUFFER_SIZE)
                if not chunk_data:
                    raise Exception("连接中断")
                    
                try:
                    chunk = pickle.loads(chunk_data)
                    if chunk['type'] == 'file_end':
                        break
                    if chunk['type'] == 'file_chunk':
                        file_data += chunk['content']
                except:
                    # 如果无法解析，可能是数据损坏，继续接收
                    file_data += chunk_data
            
            # 检查文件完整性
            if len(file_data) != file_size:
                raise Exception(f"文件不完整，期望 {file_size} 字节，收到 {len(file_data)} 字节")
            
            # 保存文件
            self.save_file(device, filename, file_data, sent=False)
            
            # 更新聊天窗口
            if device.name in self.active_chats and self.current_chat_device == device:
                chat = self.active_chats[device.name]
                self.append_message(chat["text_widget"], 
                                  f"{device.name} ({datetime.datetime.now().strftime('%H:%M:%S')}): 发送文件 '{filename}'")
                
                # 添加另存为按钮
                save_frame = Frame(chat["text_widget"].master)
                save_frame.pack(fill=X, padx=5, pady=2)
                
                ttk.Label(save_frame, text=f"收到文件: {filename}").pack(side=LEFT, padx=(0, 10))
                ttk.Button(save_frame, text="另存为", 
                          command=lambda: self.save_file_as(device, filename, file_data)).pack(side=LEFT)
                
                # 将小部件插入到文本区域
                chat["text_widget"].window_create(END, window=save_frame)
                chat["text_widget"].insert(END, "\n")
                chat["text_widget"].see(END)
            else:
                # 增加未读消息计数
                with self.devices_lock:
                    device.unread_messages += 1
                self.update_devices_listbox()
                
        except Exception as e:
            error_msg = f"文件接收错误: {e}"
            print(error_msg)
            # 发送错误通知给发送方
            try:
                sock.send(pickle.dumps({
                    'type': 'file_error',
                    'message': error_msg
                }))
            except:
                pass
    
    def save_file_as(self, device, filename, file_data):
        save_path = filedialog.asksaveasfilename(
            title="保存文件",
            initialfile=filename
        )
        
        if save_path:
            try:
                with open(save_path, 'wb') as f:
                    f.write(file_data)
                messagebox.showinfo("成功", "文件保存成功")
            except Exception as e:
                messagebox.showerror("错误", f"保存文件失败: {e}")
    
    def get_current_record_file(self, device_dir):
        """获取当前记录文件，如果超过大小则创建新文件"""
        # 获取所有记录文件
        record_files = [f for f in os.listdir(device_dir) 
                      if f.startswith("Records") and f.endswith(".dc")]
        
        if not record_files:
            return os.path.join(device_dir, "Records1.dc")
        
        # 按文件编号排序
        record_files.sort(key=lambda f: int(f[7:-3]) if f[7:-3].isdigit() else 0)
        latest_file = os.path.join(device_dir, record_files[-1])
        
        # 检查文件大小
        if os.path.getsize(latest_file) < MAX_RECORD_FILE_SIZE:
            return latest_file
        
        # 创建新文件
        file_number = 1
        if record_files:
            # 获取最大文件编号
            last_num = int(record_files[-1][7:-3])
            file_number = last_num + 1
        
        return os.path.join(device_dir, f"Records{file_number}.dc")
    
    def save_message(self, device, message, sent):
        # 使用MAC地址而不是名称作为目录名
        safe_mac = device.get_safe_mac()
        device_dir = os.path.join(self.data_dir, safe_mac)
        os.makedirs(device_dir, exist_ok=True)
        
        # 保存设备信息元数据
        device_info = {
            'mac': device.mac,
            'name': device.name,
            'last_known_name': device.name,
            'ip': device.ip
        }
        device_info_path = os.path.join(device_dir, 'device_info.json')
        with open(device_info_path, 'w', encoding='utf-8') as f:
            json.dump(device_info, f, ensure_ascii=False)
        
        # 获取当前记录文件
        record_file = self.get_current_record_file(device_dir)
        
        # 创建消息记录
        record = {
            'timestamp': time.time(),
            'sent': sent,
            'content': message,
            'sender_name': self.local_device.name if sent else device.name
        }
        
        # 追加到文件
        with open(record_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def save_file(self, device, filename, file_data, sent):
        # 使用MAC地址而不是名称作为目录名
        safe_mac = device.get_safe_mac()
        device_dir = os.path.join(self.data_dir, safe_mac)
        os.makedirs(device_dir, exist_ok=True)
        
        # 保存设备信息元数据
        device_info = {
            'mac': device.mac,
            'name': device.name,
            'last_known_name': device.name,
            'ip': device.ip
        }
        device_info_path = os.path.join(device_dir, 'device_info.json')
        with open(device_info_path, 'w', encoding='utf-8') as f:
            json.dump(device_info, f, ensure_ascii=False)
        
        # 创建文件目录
        files_dir = os.path.join(device_dir, "Files")
        os.makedirs(files_dir, exist_ok=True)
        
        # 保存文件
        filepath = os.path.join(files_dir, filename)
        
        # 如果文件已存在，添加后缀
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(filepath):
            filename = f"{base}_{counter}{ext}"
            filepath = os.path.join(files_dir, filename)
            counter += 1
        
        with open(filepath, 'wb') as f:
            f.write(file_data)
        
        # 记录文件传输
        record = {
            'timestamp': time.time(),
            'sent': sent,
            'filename': filename,
            'filepath': filepath,
            'sender_name': self.local_device.name if sent else device.name
        }
        
        # 添加到消息记录
        record_file = self.get_current_record_file(device_dir)
        with open(record_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    def load_history_messages(self, device, text_widget):
        # 使用MAC地址而不是名称作为目录名
        safe_mac = device.get_safe_mac()
        device_dir = os.path.join(self.data_dir, safe_mac)
        
        if not os.path.exists(device_dir):
            return
            
        # 获取所有记录文件
        record_files = [f for f in os.listridir(device_dir) 
                      if f.startswith("Records") and f.endswith(".dc")]
        record_files.sort(key=lambda f: int(f[7:-3]) if f[7:-3].isdigit() else 0)
        
        # 清空当前显示
        text_widget.config(state=NORMAL)
        text_widget.delete(1.0, END)
        
        # 读取所有记录
        for file in record_files:
            with open(os.path.join(device_dir, file), 'r', encoding='utf-8') as f:
                lines = f.readlines()
                
            # 显示消息
            for line in lines:
                try:
                    record = json.loads(line.strip())
                    
                    timestamp = datetime.datetime.fromtimestamp(record['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
                    sender_name = record.get('sender_name', '未知')
                    
                    if 'content' in record:
                        # 文本消息
                        self.append_message(text_widget, f"{sender_name} ({timestamp}): {record['content']}")
                    elif 'filename' in record:
                        # 文件消息
                        self.append_message(text_widget, f"{sender_name} ({timestamp}): 发送文件 '{record['filename']}'")
                        
                        if not record['sent']:
                            # 添加另存为按钮
                            save_frame = Frame(text_widget.master)
                            ttk.Label(save_frame, text=f"收到文件: {record['filename']}").pack(side=LEFT, padx=(0, 10))
                            ttk.Button(save_frame, text="另存为", 
                                      command=lambda f=record['filepath'], n=record['filename']: self.save_file_from_history(f, n)).pack(side=LEFT)
                            
                            text_widget.window_create(END, window=save_frame)
                            text_widget.insert(END, "\n")
                except:
                    pass
                
        text_widget.see(END)
        text_widget.config(state="disabled")
    
    def save_file_from_history(self, filepath, filename):
        if not os.path.exists(filepath):
            messagebox.showerror("错误", "文件不存在或已被删除")
            return
            
        save_path = filedialog.asksaveasfilename(
            title="保存文件",
            initialfile=filename
        )
        
        if save_path:
            try:
                with open(filepath, 'rb') as src, open(save_path, 'wb') as dest:
                    dest.write(src.read())
                messagebox.showinfo("成功", "文件保存成功")
            except Exception as e:
                messagebox.showerror("错误", f"保存文件失败: {e}")
    
    def append_message(self, text_widget, message):
        text_widget.config(state=NORMAL)
        text_widget.insert(END, message + "\n")
        text_widget.config(state="disabled")
        text_widget.see(END)
    
    def manage_records(self):
        # 创建记录管理窗口
        manage_win = Toplevel(self.root)
        manage_win.title("管理聊天记录")
        manage_win.geometry("400x300")
        
        # 时间选项
        ttk.Label(manage_win, text="删除超过以下时间的记录:").pack(pady=(10, 5))
        
        time_options = [
            ("7天之前", 7),
            ("14天之前", 14),
            ("30天之前", 30),
            ("60天之前", 60),
            ("180天之前", 180),
            ("所有记录", -1)
        ]
        
        selected_time = IntVar(value=7)
        
        for text, days in time_options:
            ttk.Radiobutton(manage_win, text=text, variable=selected_time, value=days).pack(anchor=W, padx=20)
        
        # 删除按钮
        ttk.Button(manage_win, text="删除记录", 
                  command=lambda: self.delete_records(manage_win, selected_time.get())).pack(pady=20)
    
    def delete_records(self, window, days):
        if days == -1:
            # 删除所有记录
            confirm = messagebox.askyesno("确认", "确定要删除所有聊天记录吗？")
            if not confirm:
                return
        else:
            confirm = messagebox.askyesno("确认", f"确定要删除{days}天之前的所有聊天记录吗？")
            if not confirm:
                return
        
        # 计算时间阈值
        threshold = time.time() - (days * 24 * 60 * 60) if days > 0 else 0
        
        # 遍历所有设备记录
        deleted_records = 0
        deleted_files = 0
        
        for device_name in os.listdir(self.data_dir):
            device_dir = os.path.join(self.data_dir, device_name)
            if not os.path.isdir(device_dir):
                continue
                
            # 处理所有记录文件
            record_files = [f for f in os.listdir(device_dir) 
                          if f.startswith("Records") and f.endswith(".dc")]
            
            for record_file in record_files:
                file_path = os.path.join(device_dir, record_file)
                
                # 读取所有记录
                with open(file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # 过滤记录
                new_lines = []
                for line in lines:
                    try:
                        record = json.loads(line.strip())
                        if record['timestamp'] >= threshold:
                            new_lines.append(line)
                        else:
                            deleted_records += 1
                            
                            # 如果是文件记录，删除文件
                            if 'filepath' in record and os.path.exists(record['filepath']):
                                try:
                                    os.remove(record['filepath'])
                                    deleted_files += 1
                                except:
                                    pass
                    except:
                        new_lines.append(line)
                
                # 重写文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.writelines(new_lines)
        
        # 显示结果
        messagebox.showinfo("完成", f"已删除 {deleted_records} 条记录和 {deleted_files} 个文件")
        
        # 刷新当前聊天窗口
        if self.current_chat_device and self.current_chat_device.name in self.active_chats:
            chat_info = self.active_chats[self.current_chat_device.name]
            text_widget = chat_info["text_widget"]
            # 重新加载历史消息
            self.load_history_messages(self.current_chat_device, text_widget)
        
        window.destroy()
    
    def filter_devices(self, event=None):
        # 更新设备列表以应用过滤
        self.update_devices_listbox()
    
    def change_avatar(self):
        # 选择新头像
        filepath = filedialog.askopenfilename(
            title="选择头像图片",
            filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.gif;*.bmp")]
        )
        
        if not filepath:
            return
            
        try:
            # 加载并处理图片
            img = Image.open(filepath)
            img.thumbnail((200, 200))  # 限制头像大小
            img = self.crop_to_circle(img)
            
            # 保存为PNG
            avatar_path = os.path.join(self.data_dir, "avatar.png")
            img.save(avatar_path, "PNG", optimize=True, quality=80)
            
            # 更新本地设备头像
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            self.local_device.avatar = img_byte_arr.getvalue()
            
            # 更新UI头像
            self.local_avatar_img = self.create_avatar_image(self.local_device.avatar, 60)
            
            # 更新左侧头像
            for widget in self.title_frame.winfo_children():
                if isinstance(widget, ttk.Label) and hasattr(widget, 'image'):
                    widget.configure(image=self.local_avatar_img)
                    widget.image = self.local_avatar_img
                    break
            
            # 广播头像更新
            with self.connections_lock:
                for sock in self.connections.values():
                    try:
                        sock.send(pickle.dumps({
                            'type': 'avatar_update',
                            'content': self.local_device.avatar_base64(),
                            'mac': self.local_device.mac
                        }))
                    except:
                        pass
            
            messagebox.showinfo("成功", "头像已更新")
        except Exception as e:
            messagebox.showerror("错误", f"无法加载图片: {e}")
    
    def on_closing(self):
        self.running = False
        
        # 关闭所有连接
        with self.connections_lock:
            for sock in self.connections.values():
                try:
                    sock.close()
                except:
                    pass
        
        # 关闭套接字
        try:
            self.udp_socket.close()
        except:
            pass
            
        try:
            self.tcp_socket.close()
        except:
            pass
            
        self.root.destroy()

if __name__ == "__main__":
    root = Tk()
    app = LanChatApp(root)
    root.mainloop()
