function varargout = getPulsingPlot(File,channelNum)
%% Constants
MU_SIGN = char(181);
FONT_SIZE = 20;
COLORS = { ...
    '#0072BD', ...
    '#D95319', ...
    '#EDB120', ...
    '#7E2F8E', ...
    '#77AC30', ...
    '#4DBEEE', ...
    '#A2142F', ...
    '#000000'};
LINE_STYLES = {'-','--',':','-.'};
LINE_WIDTH = 1.5;
PLUS_SIZE = 200;
HORZ_SIZE = 250;
SCATTER_WIDTH = 1.75;
SCATTER_COLOR = 'k';
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;
TICK_OFFSET = 1e-7;

%% Variables
% File
notebook = File.Notebook;
subject = File.Subject;
saveFolder = File.Path;
if ~isempty(subject)
    expName = [notebook '_' subject];
else
    expName = notebook;
end
folderName = expName;
folderPath = fullfile(saveFolder,folderName);

% Configuration
polarity = File.Parameters.Polarity;
activeElectrode = File.Parameters.WorkingElectrode.Type;
refElectrode = File.Parameters.ReferenceElectrode.Type;
returnElectrode = File.Parameters.CounterElectrode.Type;

% Number
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);

% Captures
capture_arr = [File.Data(1).Capture(:).Index];
captureNum = length(capture_arr);

% Pulsing
stimRate = File.Parameters.StimulationRate;
numOfPulses = File.Test.NumberOfPulses;
periodic = File.Test.Periodic;
numOfPulses_use = addCommas(numOfPulses);
numOfPeriodic = numOfPulses / periodic + 1;
% pulseNum_
pulseNum_arr = vertcat(File.Data(1).Capture(:).PulseNumber);
pulseNum = pulseNum_arr(captureNum);
pulseNum_use = addCommas(pulseNum);
% pulseLegend_cell = cell(numOfPeriodic,1);
% for pulseNum_idx = 1:captureNum
%     pulseNum_leg = pulseNum_arr(pulseNum_idx);
%     pulseNum_leg_use = addCommas(pulseNum_leg);
%     pulseLegend_cell{pulseNum_idx} = [pulseNum_leg_use ' pulses'];
% end
pulseLegend_cell = addCommas(pulseNum_arr);

% Channels
groupNum = channelNum;

% Pattern
interphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
amplitude1 = File.Data(1).Capture(captureNum).Amplitude;
chargePhase = File.Data(1).Capture(captureNum).ChargePhase; % charge per phase
chargeInjection = File.Data(1).Capture(captureNum).ChargeInjection; % charge injection
amplitude_use = sprintf('{\\itI}_{stim} = %+.2f %sA',amplitude1,MU_SIGN);
chargePhase_use = sprintf('{\\itQ}_{ph} = %.2f nC/ph',chargePhase);
if chargeInjection < 0.01
    chargeInjection_uC = chargeInjection * 1e3;
    chargeInjection_use = sprintf('{\\itQ}_{inj} = %.2g %s/cm^2',chargeInjection_uC,MU_SIGN);
else
    chargeInjection_use = sprintf('{\\itQ}_{inj} = %.2f mC/cm^2',chargeInjection);
end
phaseWidth1 = File.Parameters.PhaseWidth1;
phaseWidth2 = File.Parameters.PhaseWidth2;
isSymmetric = File.Parameters.Symmetry;
if isSymmetric
    stimRate = File.Parameters.StimulationRate;
    stimRate_commas = addCommas(stimRate);
    stimRate_use = sprintf('{\\itf}_{stim} = %s pps',stimRate_commas);
    subtitle_charge = [ ...
        amplitude_use ';  ' chargePhase_use ';  ' chargeInjection_use ';  ' ...
        stimRate_use];
    subtitleFont = FONT_SIZE - 2;
else
    phaseWidth1_comma = addCommas(phaseWidth1);
    phaseWidth1_use = sprintf('{\\itt}_{ph,1} = %s %ss',phaseWidth1_comma,MU_SIGN);
    phaseWidth2_comma = addCommas(phaseWidth2);
    phaseWidth2_use = sprintf('{\\itt}_{ph,2} = %s %ss',phaseWidth2_comma,MU_SIGN);
    subtitle_charge = [ ...
        amplitude_use ';  ' chargePhase_use ';  ' chargeInjection_use ';  ' ...
        phaseWidth1_use ';  ' phaseWidth2_use];
    subtitleFont = FONT_SIZE - 5;
end
subtitle_pulse = sprintf('Pulsing: %s / %s',pulseNum_use,numOfPulses_use);
subtitle_text = {subtitle_charge,subtitle_pulse};

% Fields
dataLength = File.Oscilloscope(1).Settings.DataLength;
oscilloscopeFields = vertcat(File.Oscilloscope(:).Fields);
activeChannel_tf = containsi(oscilloscopeFields,{'act','work','pot'});
diffChannel_tf = containsi(oscilloscopeFields,{'diff'});
voltageChannel_tf = containsi(oscilloscopeFields,{'volt'});
if any(activeChannel_tf)
    specialChannel_idx = find(activeChannel_tf);
elseif any(diffChannel_tf)
    specialChannel_idx = find(diffChannel_tf);
elseif any(voltageChannel_tf)
    specialChannel_idx = find(voltageChannel_tf);
else
    specialChannel_idx = 1;
end
specialChannel = oscilloscopeFields(specialChannel_idx);
isVoltageSpecial = contains2(specialChannel,'volt');
hasReturnChannel = contains2(oscilloscopeFields,{'ret','count'});

% Metrics
metricName_cell = {'Potential Excursion','Driving Voltage','Access Voltage','Access Resistance'};
if ~isVoltageSpecial || hasReturnChannel
    if ~isVoltageSpecial
        metricName_cell = [metricName_cell, ...
            'Active Driving Potential'];
    end
    if hasReturnChannel
        metricName_cell = [metricName_cell, ...
            'Return Driving Potential'];
    end    
end
if ~isVoltageSpecial || hasReturnChannel
    if ~isVoltageSpecial
        metricName_cell = [metricName_cell, ...
            'Active Potential Transient'];
    end
    if hasReturnChannel
        metricName_cell = [metricName_cell, ...
            'Return Potential Transient'];
    end
else
    metricName_cell = [metricName_cell, ...
        'Voltage Transient'];
end
numOfMetricNames = length(metricName_cell);

% Extract
time = File.Data(1).Capture(captureNum).Time;
potentialExcursion_mat = vertcat(File.Data(groupNum).Capture(:).PotentialExcursion);
% drivingVoltage_arr = File.Data(groupNum).Capture(idx).DrivingVoltage;
% [r,c] = size(drivingVoltage_arr);
% if r > c
%     drivingVoltage_mat = horzcat(File.Data(groupNum).Capture(:).DrivingVoltage);           % driving voltage value
% else
    drivingVoltage_mat = vertcat(File.Data(groupNum).Capture(:).DrivingVoltage);           % driving voltage value
% end
accessVoltage_mat = abs(vertcat(File.Data(groupNum).Capture(:).AccessVoltage));
accessResistance_mat = abs(vertcat(File.Data(groupNum).Capture(:).AccessResistance));
if ~isVoltageSpecial || hasReturnChannel
    drivingPotential_mat = vertcat(File.Data(groupNum).Capture(:).DrivingPotential);
end

% Potential Excursion
potentialExcursion1_arr = potentialExcursion_mat(1:captureNum,1);
if hasDischargeDelay
    potentialExcursion2_arr = potentialExcursion_mat(1:captureNum,2);
end
% Driving Potential
if ~isVoltageSpecial
    activeDriving1_mat = drivingPotential_mat(1:captureNum,1);
    activeDriving2_mat = drivingPotential_mat(1:captureNum,2);
    active_mat = horzcat(File.Data(groupNum).Capture(:).Active);
else
    voltage_mat = horzcat(File.Data(groupNum).Capture(:).Voltage);
end
if hasReturnChannel
    returnDriving1_mat = drivingPotential_mat(1:captureNum,3);
    returnDriving2_mat = drivingPotential_mat(1:captureNum,4);
    return_mat = horzcat(File.Data(groupNum).Capture(:).Return);
end

% Figure
numOfFigs = numOfGroups + numOfMetricNames;
figNum = numOfFigs - numOfMetricNames;
file_tif_cell = cell(numOfMetricNames,1);
% figNum_arr = figNum:numOfFigs;
% for fig_idx = figNum_arr
%     fig = figure(fig_idx);
%     clf;
%     cla;
%     delete(findall(fig,'type','annotation'))
    % buttonHandle = gobjects(1);
    % ax = gca;
% end
numOfColors = length(COLORS);
numOfStyles = length(LINE_STYLES);
% varargout{1} = buttonHandle;
% varargout{2} = ax;


%% Screen
% [screenWidth,screenHeight] = get(0,'Screensize');
screen = get(0,'MonitorPositions');
[numOfScreens,~] = size(screen);
if numOfScreens > 1
    screen1 = screen(1,3);
    screen2 = screen(2,3);
    if screen1 > screen2
        screenWidth = screen(1,3);
        screenHeight = screen(1,4);
        posX = screen(1,1);
        posY = screen(1,2);
    else
        screenWidth = screen(2,3);
        screenHeight = screen(2,4);
        posX = screen(2,1);
        posY = screen(2,2);
    end
else
    screenWidth = screen(3);
    screenHeight = screen(4);
    posX = screen(1);
    posY = screen(2);
end
figPosX = ceil((screenWidth - FIG_WIDTH) / 2) + posX;
figPosY = ceil((screenHeight - FIG_HEIGHT - WINDOW_HEADER) / 2) + posY;

%% Setup
fprintf('Creating plot...\n');
startTime = tic;
% buttonHandle = uicontrol(...
%     'Style','PushButton',...
%     'String','STOP',...
%     'BackgroundColor','r',...
%     'Callback','delete(gcbf)');

for metric_idx = 1:numOfMetricNames
    % Metric Name
    metricName = metricName_cell{metric_idx};
    updateWaitbar(File,['Plotting ' metricName '...']);
    fprintf('\t%s...',metricName);
    plotTime = tic;
    figNum = figNum + 1;
    fig = figure(figNum);
    delete(findall(fig,'type','annotation'));
    switch metricName
        case 'Potential Excursion'
            tag = 'PotentialExcursion';
            if hasDischargeDelay
                data = horzcat(potentialExcursion1_arr,potentialExcursion2_arr);
            else
                data = potentialExcursion1_arr;
            end
            switch polarity
                case -1
                    legend_cell = {'{\itE}_{mc}','{\itE}_{ma}'};
                case 1
                    legend_cell = {'{\itE}_{ma}','{\itE}_{mc}'};
            end
            if ~hasDischargeDelay
                legend_cell(2) = [];
            end
            legendFont = FONT_SIZE;
        case 'Driving Voltage'
            tag = 'DrivingVoltage';
            data = drivingVoltage_mat;
            legend_cell = {'{\itV}_{d,1}','{\itV}_{d,2}'};
            legendFont = FONT_SIZE;
        case 'Access Voltage'
            tag = 'AccessVoltage';
            data = accessVoltage_mat;
            if hasInterphaseDelay
                legend_cell = { ...
                '{\itV}_{al,1}','{\itV}_{at,1}', ...
                '{\itV}_{al,2}'};
            else
                legend_cell = {'{\itV}_{al,1}'};
            end
            if hasDischargeDelay
                legend_cell = [legend_cell '{\itV}_{at,2}']; %#ok<*AGROW>
            end
            legendFont = FONT_SIZE;
        case 'Access Resistance'
            tag = 'AccessResistance';
            data = accessResistance_mat;
            if hasInterphaseDelay
                legend_cell = { ...
                '{\itR}_{al,1}','{\itR}_{at,1}', ...
                '{\itR}_{al,2}'};
            else
                legend_cell = {'{\itR}_{al,1}'};
            end
            if hasDischargeDelay
                legend_cell = [legend_cell '{\itR}_{at,2}'];
            end
            legendFont = FONT_SIZE;
        case 'Active Driving Potential'
            tag = 'ActiveDrivingPotential';
            data = horzcart(activeDriving1_mat,activeDriving2_mat);
            legend_cell = {'{\itE}_{da,1}','{\itE}_{da,2}'};
            legendFont = FONT_SIZE;
        case 'Return Driving Potential'
            tag = 'ReturnDrivingPotential';
            data = horzcart(returnDriving1_mat,returnDriving2_mat);
            legend_cell = {'{\itE}_{dr,1}','{\itE}_{dr,2}'};
            legendFont = FONT_SIZE;
        case 'Active Potential Transient'
            tag = 'ActivePotentialTransient';
            data = active_mat;
            legend_cell = pulseLegend_cell;
            legendFont = FONT_SIZE - 4;
        case 'Voltage Transient'
            tag = 'VoltageTransient';
            data = voltage_mat;
            legend_cell = pulseLegend_cell;
            legendFont = FONT_SIZE - 4;
        case 'Return Potential Transient'
            tag = 'ReturnPotentialTransient';
            data = return_mat;
            legend_cell = pulseLegend_cell;
            legendFont = FONT_SIZE - 4;
    end

    % Reference Electrode
    isPotential = contains2(metricName,{'act','work','cou','ret','diff','pot'});
    if isPotential
        specialReference = refElectrode;
    else
        specialReference = returnElectrode;
    end

    % Y Name
    if contains2(metricName,'volt')
        yName = 'Voltage (V)';
    elseif contains2(metricName,'resist')
        yName = 'Resistance (k\Omega)';
    elseif isVoltageSpecial
        yName = sprintf('Voltage versus %s (V)',specialReference);
    else
        yName = sprintf('Potential versus %s (V)',specialReference);
    end
    plotName = metricName;

    if contains2(metricName,'trans')
        for captureNum = capture_arr
            % Line color
            color_idx = rem(captureNum,numOfColors);
            if color_idx == 0
                color_idx = numOfColors;
            end
            lineColor = COLORS{color_idx};
            % Line style
            multiple = ceil(captureNum / numOfColors);
            lineStyle_idx = rem(multiple,numOfStyles);
            if lineStyle_idx == 0
                lineStyle_idx = numOfStyles;
            end
            lineStyle = LINE_STYLES{lineStyle_idx};

            arr = data(1:dataLength,captureNum);
            plot(time,arr,...
                'LineStyle',lineStyle,...
                'Marker','none', ...
                'Color',lineColor,...
                'LineWidth',LINE_WIDTH, ...
                'DisplayName',plotName);
            hold on;
        end
        diffTime = mean(diff(time));
        xMin = round(min(time),1,'significant') - diffTime;
        xMax = round(max(time),1,'significant') + diffTime;
        xlim([xMin xMax]);
        xName = ['Time (' MU_SIGN 's)'];
    else
        plot(pulseNum_arr,data,...
            'LineStyle','-',...
            'Marker','.', ...
            'LineWidth',LINE_WIDTH, ...
            'DisplayName',plotName, ...
            'MarkerSize',20);
        xName = 'Number of Pulses';
    end
    hold off;

    %% Labels
    xtickformat('%,g');
    ax = gca;
    ytickformat('%,g');
    ax.YAxis.Exponent = 0;
    ylabel(yName,'Color','k');
    ylim('tickaligned');
    channelName = sprintf('Channel %d %s',channelNum,metricName);
    title(channelName);
    sub = subtitle(subtitle_text);
    leg = legend(legend_cell);
    xlabel(xName);
    set(fig,'Position',[figPosX,figPosY,FIG_WIDTH,FIG_HEIGHT]);
    set(ax,'FontSize',FONT_SIZE);
    sub.FontSize = subtitleFont;
    box on;
    leg.Location = 'best';
    leg.FontSize = legendFont;
    drawnow;
    [endTime,unit] = getEndTime(plotTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);

    updateWaitbar(File);

    fprintf('\t\t')
    fileName = [expName '_Pulsing_' tag];
    file_tif = saveChannelFigure( ...
        File, ...
        fileName, ...
        folderPath, ...
        channelNum, ...
        figNum);
    file_tif_cell{metric_idx} = file_tif;

    updateWaitbar(File);
end

percentage = updateWaitbar(File);
captureNum_use = sprintf('Percentage Complete: %.2f%%',percentage);
annotation(...
    fig,...
    'textbox',[0.015 0.05 0.01 0.01],...
    'String',captureNum_use,...
    'FontSize',8,...
    'FitBoxToText','on');
drawnow;
varargout{1} = file_tif_cell;

[endTime,unit] = getEndTime(startTime);
fprintf('Time Elapsed (%.2f %s)\n',endTime,unit);

end

