"""Guarda a chave do TMDB no Gerenciador de Credenciais do Windows (serviço "onVLC").

Abre uma caixinha para colar a chave; ela não aparece na tela nem fica em arquivo.
"""
import tkinter as tk
from tkinter import messagebox, simpledialog

import keyring

root = tk.Tk()
root.withdraw()
key = simpledialog.askstring("onVLC", "Cole o \"API Read Access Token\" do TMDB:", show="*")
if key and key.strip():
    keyring.set_password("onVLC", "tmdb_token", key.strip())
    messagebox.showinfo("onVLC", "Chave do TMDB guardada.")
