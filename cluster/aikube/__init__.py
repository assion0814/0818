"""aikube — AI K8s 集群调度网络（dsh-routing-suite / cluster 组件）。

以 k8s 集群为蓝本（博客《K8s环境搭建（保姆级教学）》的 1 主 2 从架构）：
Control-Plane(ai-apiserver + ai-etcd + ai-scheduler + ai-controller-manager)
+ Node(ai-kubelet + ai-runtime) + aikubectl/aikubeadm 风格 CLI。
全部 Python 标准库实现，单机多进程模拟，AI 调度器可插拔 LLM 分类。

组件版本对齐套装的 router-standard 路由思想：任务按 spec/react/mixed/weak 分类，
由调度器打分路由到能力匹配的节点（Pro 节点=深度思考，Flash 节点=快速执行）。
"""

__version__ = "0.1.0"
COMPONENT = "cluster"  # dsh-routing-suite 组件名
