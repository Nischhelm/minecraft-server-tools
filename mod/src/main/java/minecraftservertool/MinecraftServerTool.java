package minecraftservertool;

import net.minecraft.server.MinecraftServer;
import net.minecraft.world.DimensionType;
import net.minecraft.world.WorldServer;
import net.minecraftforge.common.DimensionManager;
import net.minecraftforge.fml.common.FMLCommonHandler;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.common.eventhandler.SubscribeEvent;
import net.minecraftforge.fml.common.gameevent.PlayerEvent;
import net.minecraftforge.fml.common.gameevent.TickEvent;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

@Mod(modid = MinecraftServerTool.MODID, version = MinecraftServerTool.VERSION, name = MinecraftServerTool.NAME, acceptableRemoteVersions = "*")
@Mod.EventBusSubscriber(modid = MinecraftServerTool.MODID)
public class MinecraftServerTool {
    public static final String MODID = "minecraftservertool";
    public static final String VERSION = "1.0.0";
    public static final String NAME = "MinecraftServerTool";
    public static final Logger LOGGER = LogManager.getLogger(NAME);

    // ~1x/min at 20 TPS; drifts with actual server speed, which is fine for
    // a monitoring cadence.
    private static final int LOG_INTERVAL_TICKS = 1200;

    private static int tickCounter = 0;

    @SubscribeEvent
    public static void onServerTick(TickEvent.ServerTickEvent event) {
        if (event.phase != TickEvent.Phase.END) return;

        tickCounter++;
        if (tickCounter % LOG_INTERVAL_TICKS != 0) return;

        MinecraftServer server = FMLCommonHandler.instance().getMinecraftServerInstance();

        double overallMspt = meanMs(server.tickTimeArray);
        double overallTps = tps(overallMspt);
        LOGGER.info("[perf] dim=overall mspt={} tps={}", format(overallMspt), format(overallTps));

        for (WorldServer world : server.worlds) {
            int dim = world.provider.getDimension();
            long[] dimTimes = server.worldTickTimes.get(dim);
            double mspt = meanMs(dimTimes);
            double dimTps = tps(mspt);
            int chunks = world.getChunkProvider().getLoadedChunkCount();
            int entities = world.loadedEntityList.size();

            LOGGER.info("[perf] dim={} mspt={} tps={} chunks={} entities={}", dimensionName(dim), format(mspt), format(dimTps), chunks, entities);
        }
    }

    @SubscribeEvent
    public static void onPlayerChangedDimension(PlayerEvent.PlayerChangedDimensionEvent event) {
        String from = dimensionName(event.fromDim);
        String to = dimensionName(event.toDim);
        LOGGER.info("[dimchange] player={} from={} to={}", event.player.getName(), from, to);
    }

    private static String dimensionName(int dim) {
        DimensionType type = DimensionManager.getProviderType(dim);
        return type != null ? type.getName() : ("dim" + dim);
    }

    private static double meanMs(long[] times) {
        long sum = 0;
        for (long t : times) {
            sum += t;
        }
        return (sum / (double) times.length) * 1.0e-6;
    }

    private static double tps(double mspt) {
        return Math.min(1000.0 / mspt, 20.0);
    }

    private static String format(double value) {
        return String.format("%.3f", value);
    }
}
