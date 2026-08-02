# PyTorch 实现 Transformer

本文用 PyTorch 从零实现 Transformer 的关键模块，所有代码基于 PyTorch 张量运算。

### QKV 投影层实现

在 PyTorch 中用 nn.Linear 实现 QKV 投影。QKV 三个线性层可以合并成一个大矩阵乘法以提升 GPU 利用率。QKV 拆分后按头数 reshape。

### 多头注意力模块

多头注意力模块 forward 先做 QKV 投影，再拆头，再算注意力权重，最后合并。多头注意力的拆头操作只是视图变换，不增加 PyTorch 计算量。多头注意力合并后接 dropout 与残差连接。

### 前馈网络与层归一化

每个 Transformer 子层后接前馈网络与层归一化。前馈网络是两层线性变换夹一个激活函数。层归一化稳定深层网络训练。

### 位置编码

Transformer 没有循环结构，需要位置编码注入顺序信息。位置编码用正弦余弦函数生成，可以外推到训练未见过的序列长度。位置编码与词嵌入相加后进入编码器。
