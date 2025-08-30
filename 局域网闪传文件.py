import socket
import threading
import json
import os
import time
import uuid
from tkinter import Tk, Label, Entry, Button, Listbox, messagebox, filedialog, Frame, Radiobutton, StringVar

class NetworkManager:
    def __init__(self):
        self.running = False
        self.tcp_server = None
        self.udp_broadcast = None
        self.udp_listener = None
        self.mode = "none"  # "server", "client", or "none"
        self.server_info = {}
        self.discovered_servers = []
        self.lock = threading.Lock()

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def get_mac_address(self):
        mac = uuid.getnode()
        return ':'.join(("%012X" % mac)[i:i+2] for i in range(0, 12, 2))

    def start_server(self, name, port=5000):
        self.mode = "server"
        self.running = True
        self.server_info = {
            "name": name,
            "ip": self.get_local_ip(),
            "mac": self.get_mac_address(),
            "port": port
        }
        
        # 启动TCP文件服务器
        threading.Thread(target=self._start_tcp_server, daemon=True).start()
        # 启动广播服务
        threading.Thread(target=self._broadcast_presence, daemon=True).start()

    def _start_tcp_server(self):
        self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_server.bind(('0.0.0.0', self.server_info['port']))
        self.tcp_server.listen(5)
        
        while self.running:
            try:
                client, addr = self.tcp_server.accept()
                threading.Thread(target=self._handle_client, args=(client,)).start()
            except:
                break

    def _handle_client(self, client):
        try:
            # 接收文件信息
            file_info = json.loads(client.recv(1024).decode())
            filename = file_info['filename']
            filesize = file_info['filesize']
            
            # 选择保存位置
            save_path = filedialog.asksaveasfilename(
                initialfile=filename,
                title="保存文件",
                filetypes=(("所有文件", "*.*"),)
            )
            
            if not save_path:
                client.close()
                return
                
            # 接收文件数据
            with open(save_path, 'wb') as f:
                received = 0
                while received < filesize:
                    data = client.recv(1024)
                    if not data:
                        break
                    f.write(data)
                    received += len(data)
            
            messagebox.showinfo("成功", f"文件 {filename} 接收完成!")
        except Exception as e:
            messagebox.showerror("错误", f"文件接收失败: {str(e)}")
        finally:
            client.close()

    def _broadcast_presence(self):
        self.udp_broadcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_broadcast.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        while self.running:
            data = json.dumps(self.server_info)
            self.udp_broadcast.sendto(data.encode(), ('<broadcast>', 9999))
            time.sleep(5)  # 每5秒广播一次

    def start_client(self):
        self.mode = "client"
        self.running = True
        # 启动服务器发现
        threading.Thread(target=self._discover_servers, daemon=True).start()

    def _discover_servers(self):
        self.udp_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_listener.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.udp_listener.settimeout(1)
        self.udp_listener.bind(('', 9999))
        
        # 发送发现请求
        self.udp_listener.sendto(b"DISCOVER", ('<broadcast>', 9999))
        
        while self.running:
            try:
                data, addr = self.udp_listener.recvfrom(1024)
                server_info = json.loads(data.decode())
                server_info['addr'] = addr[0]
                
                with self.lock:
                    # 检查是否已存在相同服务器
                    exists = False
                    for s in self.discovered_servers:
                        if s['ip'] == server_info['ip'] and s['port'] == server_info['port']:
                            exists = True
                            break
                    
                    if not exists:
                        self.discovered_servers.append(server_info)
            except socket.timeout:
                # 定期重新发送发现请求
                if self.running:
                    self.udp_listener.sendto(b"DISCOVER", ('<broadcast>', 9999))
            except:
                continue

    def get_discovered_servers(self):
        with self.lock:
            return self.discovered_servers.copy()

    def send_file(self, server, filepath):
        try:
            # 连接到服务器
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((server['ip'], server['port']))
            
            # 发送文件信息
            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)
            file_info = json.dumps({
                "filename": filename,
                "filesize": filesize
            })
            s.send(file_info.encode())
            
            # 发送文件内容
            with open(filepath, 'rb') as f:
                while True:
                    data = f.read(1024)
                    if not data:
                        break
                    s.send(data)
            
            s.close()
            return True
        except Exception as e:
            messagebox.showerror("错误", f"文件发送失败: {str(e)}")
            return False

    def stop(self):
        self.running = False
        self.mode = "none"
        
        if self.tcp_server:
            self.tcp_server.close()
            self.tcp_server = None
        
        if self.udp_broadcast:
            self.udp_broadcast.close()
            self.udp_broadcast = None
        
        if self.udp_listener:
            self.udp_listener.close()
            self.udp_listener = None
        
        with self.lock:
            self.discovered_servers = []


class FileTransferApp:
    def __init__(self):
        self.network = NetworkManager()
        self.window = Tk()
        self.window.title("局域网文件传输工具")
        self.window.geometry("600x500")
        
        # 模式选择
        self.mode_frame = Frame(self.window)
        self.mode_frame.pack(pady=20)
        
        Label(self.mode_frame, text="选择模式:").grid(row=0, column=0, padx=5)
        
        self.mode_var = StringVar(value="none")
        
        Radiobutton(self.mode_frame, text="主机模式", variable=self.mode_var, 
                   value="server", command=self.on_mode_change).grid(row=0, column=1, padx=5)
        Radiobutton(self.mode_frame, text="客户端模式", variable=self.mode_var, 
                   value="client", command=self.on_mode_change).grid(row=0, column=2, padx=5)
        
        # 服务器配置
        self.server_frame = Frame(self.window)
        Label(self.server_frame, text="主机配置").pack(pady=5)
        
        Label(self.server_frame, text="主机名称:").pack()
        self.name_entry = Entry(self.server_frame, width=30)
        self.name_entry.pack()
        self.name_entry.insert(0, "我的电脑")
        
        Label(self.server_frame, text="端口号:").pack()
        self.port_entry = Entry(self.server_frame, width=10)
        self.port_entry.pack()
        self.port_entry.insert(0, "5000")
        
        self.start_btn = Button(self.server_frame, text="启动服务", command=self.toggle_server)
        self.start_btn.pack(pady=10)
        
        self.server_info_text = Label(self.server_frame, text="", justify="left")
        self.server_info_text.pack(pady=5)
        
        # 客户端配置区域
        self.client_frame = Frame(self.window)
        Label(self.client_frame, text="客户端配置").pack(pady=5)
        
        self.refresh_btn = Button(self.client_frame, text="搜索主机", command=self.discover_servers)
        self.refresh_btn.pack(pady=5)
        
        Label(self.client_frame, text="可用的主机:").pack()
        self.server_list = Listbox(self.client_frame, width=70, height=10)
        self.server_list.pack(pady=5, padx=10)
        
        self.select_btn = Button(self.client_frame, text="选择文件并发送", 
                               command=self.send_file, state="disabled")
        self.select_btn.pack(pady=10)
        
        # 初始状态
        self.update_ui_based_on_mode()
        
        # 定期更新服务器列表
        self.schedule_server_list_update()
        
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_mode_change(self):
        # 停止当前活动
        self.network.stop()
        self.update_ui_based_on_mode()

    def update_ui_based_on_mode(self):
        mode = self.mode_var.get()
        
        # 隐藏所有框架
        self.server_frame.pack_forget()
        self.client_frame.pack_forget()
        
        if mode == "server":
            self.server_frame.pack(fill="x", padx=20, pady=10)
            self.start_btn.config(text="启动服务")
            self.server_info_text.config(text="")
        elif mode == "client":
            self.client_frame.pack(fill="x", padx=20, pady=10)
            self.refresh_btn.config(text="搜索主机", state="normal")
            self.select_btn.config(state="disabled")
            self.server_list.delete(0, 'end')

    def toggle_server(self):
        if self.network.mode == "server" and self.network.running:
            self.network.stop()
            self.start_btn.config(text="启动服务")
            self.server_info_text.config(text="服务已停止")
        else:
            name = self.name_entry.get().strip()
            if not name:
                messagebox.showerror("错误", "请输入主机名称")
                return
                
            try:
                port = int(self.port_entry.get().strip())
                if port < 1 or port > 65535:
                    raise ValueError
            except:
                messagebox.showerror("错误", "请输入有效的端口号 (1-65535)")
                return
                
            self.network.start_server(name, port)
            self.start_btn.config(text="停止服务")
            info = f"IP: {self.network.server_info['ip']}\nMAC: {self.network.server_info['mac']}\n端口: {port}"
            self.server_info_text.config(text=info)

    def discover_servers(self):
        self.server_list.delete(0, 'end')
        self.refresh_btn.config(state="disabled", text="搜索中...")
        self.select_btn.config(state="disabled")
        
        # 确保网络处于客户端模式
        if self.network.mode != "client":
            self.network.stop()
            self.network.start_client()
        
        # 清空之前的服务器列表
        self.network.discovered_servers = []

    def update_server_list(self):
        if self.network.mode != "client" or not self.network.running:
            return
            
        servers = self.network.get_discovered_servers()
        self.server_list.delete(0, 'end')
        
        for server in servers:
            self.server_list.insert('end', 
                f"{server['name']} | IP: {server['ip']} | MAC: {server['mac']} | 端口: {server['port']}")
        
        if servers:
            self.select_btn.config(state="normal")
            self.refresh_btn.config(state="normal", text="刷新列表")
        else:
            self.refresh_btn.config(state="normal", text="重新搜索")

    def schedule_server_list_update(self):
        if self.network.mode == "client" and self.network.running:
            self.update_server_list()
        self.window.after(2000, self.schedule_server_list_update)

    def send_file(self):
        selection = self.server_list.curselection()
        if not selection:
            messagebox.showwarning("提示", "请先选择一个主机")
            return
            
        servers = self.network.get_discovered_servers()
        if not servers or selection[0] >= len(servers):
            messagebox.showwarning("提示", "所选主机已不可用")
            return
            
        server = servers[selection[0]]
        filepath = filedialog.askopenfilename(title="选择要发送的文件")
        if not filepath:
            return
            
        self.select_btn.config(state="disabled", text="发送中...")
        threading.Thread(target=self._send_file, args=(server, filepath)).start()

    def _send_file(self, server, filepath):
        if self.network.send_file(server, filepath):
            messagebox.showinfo("成功", "文件发送完成!")
        self.select_btn.config(state="normal", text="选择文件并发送")

    def on_closing(self):
        self.network.stop()
        self.window.destroy()

    def run(self):
        self.window.mainloop()


if __name__ == "__main__":
    app = FileTransferApp()
    app.run()