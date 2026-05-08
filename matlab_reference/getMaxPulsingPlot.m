function maxPulsingPlot = getMaxPulsingPlot(file)
%% Constants
FIRST_COLOR = '#0072BD';
SECOND_COLOR = '#D95319';
THIRD_COLOR = '#EDB120';

%% Variables
% time = time / 1e-6;
% [screenWidth,screenHeight] = get(0,'Screensize');
screen = get(0,'MonitorPositions');
screenWidth = screen(1,3);
screenHeight = screen(1,4);
figWidth = 800;
figHeight = 600;
windowHead = 60;
figPosX = ceil((screenWidth - figWidth) / 2);
figPosY = ceil((screenHeight - figHeight - windowHead) / 2);


%% Function
fprintf('Creating plot...');

maxPulsingPlot = figure;
clf;
delete(findall(gcf,'type','annotation'));

pulsingData = file.PulsingData;
[count,~] = size(pulsingData);

pulseNum_array = [];
chargeInj_mean_array = [];
chargeInj_sd_array = [];
percentage_mean_array = [];
percentage_sd_array = [];
for idx = 1:count
    % pulse number
    pulseNum = pulsingData(idx).PulseNumber;
    pulseNum_array_alloc = [pulseNum_array,pulseNum];
    pulseNum_array = pulseNum_array_alloc;

    % charge injection
    chargeInj = pulsingData(idx).ChargeInjection;
    chargeInj_mean = mean(chargeInj);
    chargeInj_mean_array_alloc = [chargeInj_mean_array,chargeInj_mean];
    chargeInj_mean_array = chargeInj_mean_array_alloc;
    chargeInj_sd = std(chargeInj);
    chargeInj_sd_array_alloc = [chargeInj_sd_array,chargeInj_sd];
    chargeInj_sd_array = chargeInj_sd_array_alloc;

    % percentage
    percentage = pulsingData(idx).Percentage;
    percentage_max = max(percentage);
    percentage_mean = mean(percentage);
    percentage_mean_array_alloc = [percentage_mean_array,percentage_mean];
    percentage_mean_array = percentage_mean_array_alloc;
    percentage_sd = std(percentage);
    percentage_sd_array_alloc = [percentage_sd_array,percentage_sd];
    percentage_sd_array = percentage_sd_array_alloc;
end

title_charge = sprintf('Max {\\itQ}_{inj} Pulsing');
title(title_charge);
set(gca,'XColor','k');
xlabel('Pulses');
if count == 1
    xlim([0 1]);
end

yyaxis left
errorbar(...
    pulseNum_array,...
    chargeInj_mean_array,chargeInj_sd_array,...
    'Marker','.',...
    'LineWidth',2);
set(gca,'YColor','k');
ylabel('Max {\itQ}_{inj} (\muC/cm^2)',...
    'Color',FIRST_COLOR);
ylim([0 inf]);

yyaxis right
errorbar(...
    pulseNum_array,...
    percentage_mean_array,percentage_sd_array,...
    'Marker','.',...
    'LineWidth',2);
set(gca,'YColor','k');
ylabel('Percentage (%)',...
    'Color',SECOND_COLOR,...
    'Rotation',-90,....
    'HorizontalAlignment','center',...
    'VerticalAlignment','bottom');
if percentage_max > 100
    ylim([0 inf]);
else
    ylim([0 100]);
end
set(gcf,'Position',[figPosX,figPosY,figWidth,figHeight]);
set(gca,'FontSize',20);
box on;
drawnow;
fprintf('OK.\n');

end