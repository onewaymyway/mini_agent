#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理所有测试浏览器实例"""
import subprocess
import json
import sys

def main():
    # 获取所有实例
    result = subprocess.run([sys.executable, 'browser_launch.py', '--list-dedicated'], 
                          capture_output=True, text=True, encoding='utf-8')
    try:
        instances = json.loads(result.stdout)
    except:
        print(f"Failed to parse instance list: {result.stdout}")
        return
    
    # 停止所有实例
    for inst in instances:
        name = inst.get('name', '')
        if name and inst.get('alive', False):
            print(f"Stopping: {name} (port {inst.get('port')})")
            subprocess.run([sys.executable, 'browser_launch.py', '--stop-dedicated', name], 
                          capture_output=True)
    
    print("清理完成")

if __name__ == '__main__':
    main()
