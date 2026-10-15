from . import models


def _ke_pos_post_init(env):
    env["account.cash.rounding"]._ke_setup_kes_rounding()
