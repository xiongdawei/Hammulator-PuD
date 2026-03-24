import math

def calculate_flip_rate(hc, hc_first=2000, hc_last=10000, rate_last=1.0):
    """
    根据论文的 Observation 4 (对数线性关系) 计算位翻转概率
    """
    # 1. 阈值保护：还没达到最低锤击次数时，概率为 0
    if hc <= hc_first:
        return 0.0
        
    # 2. 初始化极小概率以避免 log10(0)
    rate_first = 1e-8
    
    # 3. 转换到对数空间
    log_hc_first = math.log10(hc_first)
    log_hc_last = math.log10(hc_last)
    log_rate_first = math.log10(rate_first)
    log_rate_last = math.log10(rate_last)
    
    # 4. 计算对数直线的斜率和截距
    slope = (log_rate_last - log_rate_first) / (log_hc_last - log_hc_first)
    intercept = log_rate_first - (slope * log_hc_first)
    
    # 5. 计算当前概率并还原回十进制
    log_current_rate = (slope * math.log10(hc)) + intercept
    calculated_flip_rate = 10.0 ** log_current_rate
    
    # 6. 设置物理上限 (Hard Cap)
    row_flip_rate = min(calculated_flip_rate, rate_last)
    
    return row_flip_rate

# ==========================================
# 1. 打印简单的文本数据来查看趋势
# ==========================================
print(f"{'锤击次数 (HC)':<15} | {'位翻转概率 (Probability)'}")
print("-" * 45)

# 我们测试一系列的锤击次数，从远低于阈值，到超过最大阈值
test_hcs = [1000, 2000, 2500, 4000, 6000, 8000, 9500, 10000, 12000]

for hc in test_hcs:
    prob = calculate_flip_rate(hc)
    # 格式化输出，保留8位小数
    print(f"{hc:<15} | {prob:.8f} ({prob * 100:.6f}%)")

# ==========================================
# 2. (可选) 画出概率曲线图直观感受效果
# ==========================================
try:
    import matplotlib.pyplot as plt
    import numpy as np
    
    # 生成 0 到 12000 之间的数据点
    x_values = np.linspace(0, 12000, 500)
    y_values = [calculate_flip_rate(x) for x in x_values]
    
    plt.figure(figsize=(10, 6))
    plt.plot(x_values, y_values, 'b-', linewidth=2, label='RowHammer Flip Probability')
    plt.axvline(x=2000, color='r', linestyle='--', label='HC_first (2000)')
    plt.axvline(x=10000, color='g', linestyle='--', label='HC_last (10000)')
    
    plt.title('RowHammer Bit Flip Probability vs. Hammer Count')
    plt.xlabel('Hammer Count (HC)')
    plt.ylabel('Probability of Bit Flip')
    plt.grid(True)
    plt.legend()
    plt.savefig('rowhammer_flip_probability.png')
except ImportError:
    print("\n提示: 如果你安装了 matplotlib (pip install matplotlib numpy)，运行此脚本还能看到可视化曲线！")