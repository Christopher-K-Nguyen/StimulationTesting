function varargout = getVoltageTransientPlot(File,varargin)
%% Constants
MU_SIGN = char(181);
FONT_SIZE = 20;
% colors_cell = { ...
%     '#0072BD', ...
%     '#808080', ...
%     '#D95319', ...
%     '#EDB120', ...
%     '#7E2F8E', ...
%     '#77AC30', ...
%     '#4DBEEE', ...
%     '#A2142F'};
colors_cell = { ...
    '#EDB120', ...
    '#0072BD', ...
    '#7E2F8E', ...
    '#77AC30', ...
    '#FFFF00', ...
    '#00FFFF', ...
    '#FF00FF', ...
    '#00FF00'};
LINE_WIDTH = 2;
PLUS_SIZE = 200;
HORZ_SIZE = 250;
SCATTER_WIDTH = 1.75;
SCATTER_COLOR = 'k';
FIG_WIDTH = 1024;
FIG_HEIGHT = 576;
WINDOW_HEADER = 60;
TICK_OFFSET = 1e-7;

%% Variables
% Configuration
polarity = File.Parameters.Polarity;
switch polarity
    case -1
        pol1_sign = '-';
        pol2_sign = '+';
        electrodePol1_sign = 'c';
        electrodePol2_sign = 'a';
    case 1
        pol1_sign = '+';
        pol2_sign = '-';
        electrodePol1_sign = 'a';
        electrodePol2_sign = 'c';
end
activeElectrode = File.Parameters.WorkingElectrode.Type;
refElectrode = File.Parameters.ReferenceElectrode.Type;
returnElectrode = File.Parameters.CounterElectrode.Type;
returnInterpulse = 0;

% Channels
if isempty(varargin)
    channel_arr = [File.Data(:).ActiveChannel];
    groupNum = length(channel_arr);
    channelNum = channel_arr(groupNum);
else
    groupNum = varargin{1};
    channelNum = groupNum;
end

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');

% Captures
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);
Capture = File.Data(groupNum).Capture(captureNum);

% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'SP','LP'});
isTriphasic = contains2(expType,'TV');
polMethod = File.Parameters.PolarizationMethod;
isPolAtTime = contains2(polMethod,'time');
if isPolAtTime
    polType = 'time';
else
    polType = 'diff';
end

% Fields
field_cell = fieldnames(Capture);
timeField_idx = find(containsi(field_cell,'Time'));
currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
dataFields_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
numOfDataFields = length(dataFields_cell);
activeField_tf = containsi(dataFields_cell,{'act','work','pot'});
hasActiveField = any(activeField_tf);
diffField_tf = containsi(dataFields_cell,{'diff'});
voltageField_tf = containsi(dataFields_cell,{'volt'});
if hasActiveField
    specialField_idx = find(activeField_tf);
elseif any(diffField_tf)
    specialField_idx = find(diffField_tf);
elseif any(voltageField_tf)
    specialField_idx = find(voltageField_tf);
else
    specialField_idx = 1;
end
specialField = dataFields_cell{specialField_idx};

% Fields
oscilloscopeFields = vertcat(File.Oscilloscope(:).Fields);
activeChannel_tf = containsi(oscilloscopeFields,{'act','work','pot'});
hasActiveScope = any(activeChannel_tf);
diffChannel_tf = containsi(oscilloscopeFields,{'diff'});
hasDiffScope = any(diffField_tf);
voltageChannel_tf = containsi(oscilloscopeFields,{'volt'});
hasVoltageScope = any(voltageField_tf);
returnField_tf = containsi(dataFields_cell,{'ret','count'});
if hasActiveScope
    specialChannel_idx = find(activeChannel_tf);
elseif hasDiffScope
    specialChannel_idx = find(diffChannel_tf);
elseif hasVoltageScope
    specialChannel_idx = find(voltageChannel_tf);
else
    specialChannel_idx = 1;
end
specialChannel = oscilloscopeFields(specialChannel_idx);
isVoltageSpecial = contains2(specialChannel,'volt');
hasReturnChannel = contains2(oscilloscopeFields,{'ret','count'});
if hasReturnChannel
    returnField_name = dataFields_cell{returnField_tf};
end
hasAltActive = isVoltageSpecial && hasReturnChannel;
hasActive = hasAltActive || hasActiveScope || hasDiffScope;

% Pattern
amplitude = File.Data(groupNum).Capture(captureNum).Amplitude;
chargePhase = File.Data(groupNum).Capture(captureNum).ChargePhase; % charge per phase
chargeInjection = File.Data(groupNum).Capture(captureNum).ChargeInjection; % charge injection
amplitude_use = sprintf('{\\itI}_{stim} = %+.2f %sA',amplitude,MU_SIGN);
chargePhase_use = sprintf('{\\itQ}_{ph} = %.2f nC/ph',chargePhase);
if chargeInjection < 0.01
    chargeInjection_uC = chargeInjection * 1e3;
    chargeInjection_use = sprintf('{\\itQ}_{inj} = %.2g %s/cm^2',chargeInjection_uC,MU_SIGN);
else
    chargeInjection_use = sprintf('{\\itQ}_{inj} = %.2f mC/cm^2',chargeInjection);
end
amplitude1 = File.Parameters.Amplitude1(groupNum);
amplitude2 = File.Parameters.Amplitude2(groupNum);
phaseWidth1 = File.Parameters.PhaseWidth1;
phaseWidth2 = File.Parameters.PhaseWidth2;
if isTriphasic
    amplitude3 = File.Parameters.Amplitude3(groupNum);
    phaseWidth3 = File.Parameters.PhaseWidth3;
else
    amplitude3 = [];
    phaseWidth3 = 0;
end
amplitude_arr = abs([amplitude1 amplitude2 amplitude3]);
[~,amplitude_idx] = sort(amplitude_arr,'descend');
driving_idx = amplitude_idx(1);
phaseWidth_ratio_arr = File.Parameters.PhaseWidthRatio;
phaseWidth_ratio = phaseWidth_ratio_arr(2) / phaseWidth_ratio_arr(1);
% amplitude_ratio = amplitude2_mag / amplitude1_mag;
interphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
afterPhase2 = phaseWidth1 + interphaseDelay + phaseWidth2;
depolTime = File.Parameters.Depolarization;
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion2_time = afterPhase2 + depolTime;
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
if isTriphasic
    afterPhase3 = afterPhase2 + interphaseDelay + phaseWidth3;
    potentialExcursion3_time = afterPhase3 + depolTime;
end

pulseWidth_arr = File.Parameters.PulseWidth;
numOfPulseWidth = length(pulseWidth_arr);
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
    if isTriphasic
        phaseWidth3_comma = addCommas(phaseWidth3);
        phaseWidth3_use = sprintf('{\\itt}_{ph,3} = %s %ss',phaseWidth3_comma,MU_SIGN);
        subtitle_charge = [ ...
            amplitude_use ';  ' chargePhase_use ';  ' chargeInjection_use ';  ' ...
            phaseWidth1_use ';  ' phaseWidth2_use '; ' phaseWidth3_use];
        subtitleFont = FONT_SIZE - 7;
    else
        subtitle_charge = [ ...
            amplitude_use ';  ' chargePhase_use ';  ' chargeInjection_use ';  ' ...
            phaseWidth1_use ';  ' phaseWidth2_use];
        subtitleFont = FONT_SIZE - 5;
    end
end

% Amplitude
% channelGroup_mat = File.Test.Groups;
% [numOfGroups,~] = size(channelGroup_mat);
% amplitude1_arr = [File.Data(groupNum).Capture(:).Amplitude];
% amplitude1List = transpose(amplitude1_arr);
% currentStimList = abs(amplitude1List(1:captureNum));
% currentStim_min = min(currentStimList);
% currentStim_max  = max(currentStimList);
% currentStim_range = currentStim_min:0.1;currentStim_max;
% switch polarity
%     case -1
%         if hasDischargeDelay
%             legend_cell = {'Cathodic','Anodic'};
%         else
%             legend_cell = {'Cathodic'};
%         end
%     case 1
%         if hasDischargeDelay
%             legend_cell = {'Anodic','Cathodic'};
%         else
%             legend_cell = {'Anodic'};
%         end
% end
% excursionBothList = vertcat(File.Data(groupNum).Capture(:).PotentialExcursion);

annotFontSize = 8.5;
if phaseWidth1 < 100
    annotFontSize = annotFontSize - 0.5;
end

% Data
chargePhase = File.Data(groupNum).Capture(captureNum).ChargePhase;
time = File.Data(groupNum).Capture(captureNum).Time;
time0_idx = find(time <= 0,1,'last');
timeScale = File.Oscilloscope(1).HorizontalScale;
empty_tf = zeros(numOfDataFields,1);
fieldsUsed_cell = dataFields_cell;
colorsUsed_cell = colors_cell;
for dataFieldNum = 1:numOfDataFields
    field = dataFields_cell{dataFieldNum};
    try
        data = File.Data(groupNum).Capture(captureNum).(field);
    catch
        data = [];
    end
    empty_tf(dataFieldNum) = isempty(data);
end
empty_idx = flip(find(empty_tf));
if ~isempty(empty_idx)
    fieldsUsed_cell(empty_idx) = [];
    colorsUsed_cell(empty_idx) = [];
end
numOfFieldsUsed = length(fieldsUsed_cell);
isCurrentField_tf = containsi(fieldsUsed_cell,'curr');
hasCurrent = any(isCurrentField_tf);
isVoltField_idx = find(~isCurrentField_tf);
numOfVoltFields = length(isVoltField_idx);
isSpecialFound = contains2(fieldsUsed_cell,specialField);
isActiveField_tf = containsi(fieldsUsed_cell,{'act','work','pot'});
isActiveFound = any(isActiveField_tf);
isReturnField_tf = containsi(fieldsUsed_cell,{'ret','count'});
isReturnFound = any(isReturnField_tf);
% returnDiff = [];
try
    isAtVoltageCompliance = File.Data(groupNum).Capture(captureNum).Status.VoltageCompliance;
catch
    isAtVoltageCompliance = false;
end
try
    capacitance = File.Data(groupNum).Capture(captureNum).Capacitance;
catch
    capacitance = [];
end

% Figure
fig = figure(groupNum);
clf;
cla;
delete(findall(fig,'type','annotation'));
buttonHandle = gobjects(1);
ax = gca;
varargout{1} = buttonHandle;
varargout{2} = ax;
annotDims1 = [];
annotDims2 = [];
if isTriphasic
    annotDims3 = [];
end

% Waveform Data
if isSpecialFound
    % Driving Voltage
    voltageValue_arr_voltage = File.Data(groupNum).Capture(captureNum). ...
        Measurement.VoltageValues.Voltage;
    drivingVoltage1 = voltageValue_arr_voltage(1);
    drivingVoltage2 = voltageValue_arr_voltage(2);
    
    % Access Voltage
    accessVoltage1 = abs(voltageValue_arr_voltage(3));
    
    % Access Resistance
    accessResistance_arr = File.Data(groupNum).Capture(captureNum).AccessResistance;
    accessResistance1 = accessResistance_arr(1);
    
    % Plot
    voltageValue_arr_time = File.Data(groupNum).Capture(captureNum). ...
        Measurement.VoltageValues.Time;
    drivingVoltage1_time = voltageValue_arr_time(1);
    drivingVoltage2_time = voltageValue_arr_time(2);
    if isTriphasic
        drivingVoltage3_time = voltageValue_arr_time(3);
        idx = 1;
    else
        drivingVoltage3_time = [];
        idx = 0;
    end
    accessVoltage1_time = voltageValue_arr_time(3+idx);
    
    voltagePlot_arr = File.Data(groupNum).Capture(captureNum). ...
        Measurement.VoltageValues.Plot;
    drivingVoltage1_plot = voltagePlot_arr(1);
    drivingVoltage2_plot = voltagePlot_arr(2);
    if isTriphasic
        drivingVoltage3_plot = voltagePlot_arr(3);
    else
        drivingVoltage3_plot = [];
    end
    accessVoltage1_plot = voltagePlot_arr(3+idx);

    
    % Interphase Delay
    if hasInterphaseDelay
        % voltage
        accessVoltage2 = abs(voltageValue_arr_voltage(4+idx));
        accessVoltage3 = abs(voltageValue_arr_voltage(5+idx));
        
        % plot
        accessVoltage2_time = voltageValue_arr_time(4+idx);
        accessVoltage3_time = voltageValue_arr_time(5+idx);
        accessVoltage2_plot = voltagePlot_arr(4+idx);
        accessVoltage3_plot = voltagePlot_arr(5+idx);

        % resistance
        accessResistance2 = accessResistance_arr(2);
        accessResistance3 = accessResistance_arr(3);
    end

    if hasDischargeDelay
        accessVoltage4 = abs(voltageValue_arr_voltage(6));
        accessResistance4 = accessResistance_arr(4);
        accessVoltage4_time = voltageValue_arr_time(6);
        accessVoltage4_plot = voltagePlot_arr(6);
    end

    if isTriphasic
        accessVoltage4 = abs(voltageValue_arr_voltage(6));
        accessResistance4 = accessResistance_arr(4);
        accessVoltage4_time = voltageValue_arr_time(6);
        accessVoltage4_plot = voltagePlot_arr(6);
    else
    end

    % Potential Excursion
    % phase 1
    potentialExcursion_arr_time = File.Data(groupNum).Capture(captureNum). ...
        Measurement.PotentialExcursion.Time;
    potentialExcursion1_time = potentialExcursion_arr_time(1);
    potentialExcursion_arr_voltage = File.Data(groupNum).Capture(captureNum). ...
        Measurement.PotentialExcursion.Voltage;
    potentialExcursion1 = potentialExcursion_arr_voltage(1);
    % phase 2
    if isTriphasic
        if hasInterphaseDelay
            potentialExcursion2_time = potentialExcursion_arr_time(2);
            potentialExcursion2 = potentialExcursion_arr_voltage(2);
        else
            potentialExcursion2_time = [];
            potentialExcursion2 = [];
        end
        if hasDischargeDelay
            potentialExcursion3_time = potentialExcursion_arr_time(3);
            potentialExcursion3 = potentialExcursion_arr_voltage(3);
        else
            potentialExcursion3_time = [];
            potentialExcursion3 = [];
        end
    else
        if hasDischargeDelay
            potentialExcursion2_time = potentialExcursion_arr_time(2);
            potentialExcursion2 = potentialExcursion_arr_voltage(2);
        else
            potentialExcursion2_time = [];
            potentialExcursion2 = [];
        end
    end
end
isLimitReached = File.Data(groupNum).Capture(captureNum).Status.PotentialLimit;

% Return
fieldsList = vertcat(File.Oscilloscope(:).Fields);
hasReturnChannel = contains2(fieldsList,{'ret','count'});
if hasReturnChannel
    returnField_tf = containsi(fieldsList,{'ret','count'});
    returnField_name = fieldsList{returnField_tf};
end

if isReturnFound %&& (isBP || isTP)
    returnExcursion_arr = File.Data(groupNum).Capture(captureNum).ReturnExcursion;
    returnExcursion1 = returnExcursion_arr(1);
    returnExcursion2 = returnExcursion_arr(2);
    if isTriphasic
        returnExcursion3 = returnExcursion_arr(3);
    end
end

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
if numOfFieldsUsed > 0
    fprintf('Creating plot with...');
    startTime = tic;
    buttonHandle = uicontrol(...
        'Style','PushButton',...
        'String','STOP',...
        'BackgroundColor','r',...
        'Callback','delete(gcbf)');

    for fieldNum = 1:numOfFieldsUsed
        field = fieldsUsed_cell{fieldNum};
        updateWaitbar(File,['Plotting ' field '...']);
        fprintf('%s...',field);
        isPotential = contains2(field,{'act','work','cou','ret','diff','pot'});
        isVoltage = contains2(field,{'volt'});
        isCurrent = contains2(field,{'curr'});
        if isPotential
            if contains2(field,{'cou','ret'})
                if isBP
                    plotName = sprintf('%s ({\\bf%s''} versus %s)',field,returnElectrode,refElectrode);
                elseif isTP
                    plotName = sprintf('%s ({\\bf%s''''} versus %s)',field,returnElectrode,refElectrode);
                elseif isCG
                    plotName = sprintf('%s ({\\bf%s*} versus %s)',field,returnElectrode,refElectrode);
                else
                    plotName = sprintf('%s (%s versus %s)',field,returnElectrode,refElectrode);
                end
            else
                if contains2(refElectrode,{'Ag','SS','Ti'})
                    plotName = sprintf('%s (%s versus %s)',field,activeElectrode,refElectrode);
                else
                    plotName = sprintf('Voltage (%s versus %s)',activeElectrode,refElectrode);
                end
            end
        elseif isVoltage
            if isBP
                plotName = sprintf('%s (%s versus {\\bf%s''})',field,activeElectrode,returnElectrode);
            elseif isTP
                plotName = sprintf('%s (%s versus {\\bf%s''''})',field,activeElectrode,returnElectrode);
            elseif isCG
                plotName = sprintf('%s (%s versus {\\bf%s*})',field,activeElectrode,returnElectrode);
            else
                plotName = sprintf('%s (%s versus %s)',field,activeElectrode,returnElectrode);
            end
        elseif isCurrent
            plotName = 'Current Density';
        end
        plotName = strrep(plotName,'Ox','O_{x}');

        if isCurrent
            data = File.Data(groupNum).Capture(captureNum).CurrentDensity;
            yyaxis right;
            lineStyle = '-';
        else
            data = File.Data(groupNum).Capture(captureNum).(field);
            if hasCurrent
                yyaxis left;
            end
            lineStyle = '-';
        end
        lineColor = colorsUsed_cell{fieldNum};
        if contains2(field,{'cou','ret'})
            returnColor = lineColor;
            returnName = plotName;
        end
        plot(time,data,...
            'LineStyle',lineStyle,...
            'Marker','none', ...
            'Color',lineColor,...
            'LineWidth',LINE_WIDTH, ...
            'DisplayName',plotName);
        xtickformat('%,g');
        ax = gca;
        if isCurrent
            if max(abs(data)) < 1
                ytickformat('%.1f');
            else
                ax.YAxis(2).Exponent = 0;
                ytickformat('%g');
            end
            set(ax,'YColor','k');
            ylabel2('Current Density (A/cm^2)','k');
        else
            ytickformat('%,g');
            ax.YAxis(1).Exponent = 0;
            if numOfVoltFields > 1
                yName = 'Voltage (V)';
                color = 'black';
                
            else
                if isPotential
                    if isBP
                        yName = sprintf('Potential versus {\\bf%s''} (V)',refElectrode);
                    elseif isTP
                        yName = sprintf('Potential versus {\\bf%s''''} (V)',refElectrode);
                    else
                        if isVoltageSpecial
                            yName = sprintf('Voltage versus %s (V)',refElectrode);
                        else
                            yName = sprintf('Potential versus %s (V)',refElectrode);
                        end
                    end
                elseif isVoltage
                    if isBP
                        yName = sprintf('Voltage versus {\\bf%s''} (V)',returnElectrode);
                    elseif isTP
                        yName = sprintf('Voltage versus {\\bf%s''''} (V)',returnElectrode);
                    else
                        % if isVoltageSpecial
                            yName = sprintf('Voltage versus %s (V)',returnElectrode);
                        % else
                        %     yName = sprintf('Voltage versus %s (V)',returnElectrode);
                        % end
                    end
                end
                
                color = lineColor;
            end
            set(ax,'YColor','k');
            ylabel(yName,'Color',color);
        end
        drawnow;
        hold on;

        if hasCurrent && isCurrent
            primary   = 'right';
            pad       = 0;
        else
            primary   = 'left';
            pad       = TICK_OFFSET;
            if hasCurrent
                yyaxis('left');  % make sure left is active when plotting voltages
            end
        end

        % — 1) always force the primary axis symmetric about zero —
        if hasCurrent && isCurrent
            yyaxis(primary);
        end
        ylim('tickaligned');
        ylim_left = ylim;
        ylim_left_mag  = max(abs(ylim_left)) + pad;
        ylim_left_min = -ylim_left_mag;
        ylim_left_max = ylim_left_mag;
        ylim([ylim_left_min ylim_left_max]);

        % — 2) if we want “zero‐aligned” instead of symmetric, adjust the opposite side —
        if ~isSymmetric && hasCurrent && isCurrent
            % grab the right‐axis limits
            yyaxis('right');
            ylim_right = ylim;
            ylim_right_ratio  = ylim_right(1) / ylim_right(2);          % bottom/top ratio
            % compute the new left limits that line up zero
            yyaxis('left');
            ylim_left = ylim;
            ylim_left_min = ylim_left(1);
            ylim_left_max = ylim_left(2);
            % two candidates—pick the one that actually contains the old data
            ylim_left_check = ylim_left_max * ylim_right_ratio;
            if ylim_left_check > ylim_left_min
                ylim_left_min = ylim_left_check;
                ylim_left_new = [ylim_left_min ylim_left_max];
            else
                ylim_left_max = ylim_left_min / ylim_right_ratio;
                ylim_left_new = [ylim_left_min ylim_left_max];
            end
            ylim(ylim_left_new);
        end

        %% Values
        if chargePhase > 0 && isSpecialFound
            if hasCurrent
                yyaxis left;
            end
            capacitance_round = round(capacitance,2,'significant');
            capacitance_use = addCommas(capacitance_round);
            capacitance_text = sprintf('{\\itC} = %s pF',capacitance_use);    % C text
            if ~isAtVoltageCompliance
                hold on;
                % Annotation
                % phase 1
                potentialExcursion1_text = sprintf(...
                    '{\\itE}_{m%s}(%s) = %.3f V',...
                    electrodePol1_sign,polType,potentialExcursion1);     % Emc text
                if isReturnFound %&& (isBP || isTP)
                    returnExcursion1_text = sprintf(...
                        '{\\itE}{\\bf''}_{m%s}(%s) = %.3f V',...
                        electrodePol2_sign,polType,returnExcursion1);     % Emc text
                end
                drivingVoltage1_text = sprintf(...
                    '{\\itV}_{d,1} = %.3f V', ...
                    drivingVoltage1); % Vdrive text
                accessVoltage1_text = sprintf(...
                    '{\\itV}_{al,1} = %.3f V', ...
                    accessVoltage1);    % Vacc text
                accessResistance1_text = sprintf(...
                    '{\\itR}_{al,1} = %.1f k\\Omega', ...
                    accessResistance1);
                if hasInterphaseDelay
                    accessVoltage2_text = sprintf(...
                        '{\\itV}_{at,2} = %.3f V', ...
                        accessVoltage2);    % Vacc text
                    accessResistance2_text = sprintf(...
                        '{\\itR}_{at,2} = %.1f k\\Omega', ...
                        accessResistance2);
                    if isReturnFound %&& (isBP || isTP)
                        annot1_text_part = { ...
                            potentialExcursion1_text, ...
                            returnExcursion1_text, ...
                            drivingVoltage1_text,...
                            accessVoltage1_text,...
                            accessVoltage2_text,...
                            accessResistance1_text,...
                            accessResistance2_text};
                    else
                        annot1_text_part = { ...
                            potentialExcursion1_text, ...
                            drivingVoltage1_text,...
                            accessVoltage1_text,...
                            accessVoltage2_text,...
                            accessResistance1_text,...
                            accessResistance2_text};
                    end
                else
                    annot1_text_part = { ...
                        potentialExcursion1_text, ...
                        drivingVoltage1_text,...
                        accessVoltage1_text,...
                        accessResistance1_text};
                end                
                if any(capacitance)
                    annot1_text = [annot1_text_part capacitance_text];
                else
                    annot1_text = annot1_text_part;
                end

                if isReturnFound
                    % if phaseWidth1 < 100
                    %     returnIP_dims = [0.35 0.705 0.1 0.1];
                    % else
                    % if phaseWidth1 < 100
                    %     switch polarity
                    %         case -1
                    %             returnIP_dims = [0.5 0.215 0.1 0.1];
                    %         case 1
                    %             returnIP_dims = [0.5 .71 0.1 0.1];
                    %     end
                    % else
                        switch polarity
                            case 1
                                returnIP_dims = [0.57 .71 0.1 0.1]; % 0.145x`
                            case -1
                                returnIP_dims = [0.57 0.215 0.1 0.1];
                        end
                    % end

                    return_arr = File.Data(groupNum).Capture(captureNum).(returnField_name);
                    return_range = abs(max(return_arr) - min(return_arr));
                    return_amplitude = return_range / 2;
                    before_tf = time < 0;
                    returnInterpulse = mean(return_arr(before_tf)); % File.Parameters.CounterElectrode.OpenCircuitPotential
                    returnIP_text = sprintf('Return {\\itE}_{ip} = %+.3f V',returnInterpulse);
                    % returnDiff_text = sprintf('Return Difference = %s%.3f V',char(177),return_amplitude);
                    returnDiff_text = sprintf('Return Range = %.3f V',return_range);
                    return_text_part = {returnIP_text,returnDiff_text};
                    if hasActive
                        active_arr = File.Data(groupNum).Capture(captureNum).Active;
                        activeInterpulse = mean(active_arr(before_tf));
                        drivingTime = voltageValue_arr_time(driving_idx);
                        drivingVoltage = abs(voltagePlot_arr(driving_idx));
                        drivingVoltage_tf = time == drivingTime;
                        activeDriving = active_arr(drivingVoltage_tf);
                        returnDriving = return_arr(drivingVoltage_tf);
                        activeDriving_mag = abs(activeInterpulse - activeDriving);
                        returnDriving_mag = abs(returnInterpulse - returnDriving);
                        activeReturn_ratio = abs(returnDriving_mag / activeDriving_mag);
                        activeReturnRatio_text = sprintf( ...
                            'Return/Active Polarization = %.3f',activeReturn_ratio);
                        returnPercentage = returnDriving_mag / drivingVoltage * 100;
                        activePercentage = activeDriving_mag / drivingVoltage * 100;
                        if returnPercentage > 1
                            returnPercentage_text = sprintf( ...
                                'Return/Driving Voltage = %.1f %%',returnPercentage);
                        else
                            returnPercentage_text = sprintf( ...
                                'Return/Driving Voltage = %.2g %%',returnPercentage);
                        end
                        if activePercentage > 1
                            activePercentage_text = sprintf( ...
                                'Active/Driving Voltage = %.1f %%',activePercentage);
                        else
                            activePercentage_text = sprintf( ...
                                'Active/Driving Voltage = %.2g %%',activePercentage);
                        end
                        return_text = [ ...
                            return_text_part, ...
                            activeReturnRatio_text, ...
                            returnPercentage_text, ...
                            activePercentage_text];
                    else
                        return_text = return_text_part;
                    end
                    
                    returnAnnot = annotation(...
                        fig,...
                        'textbox',returnIP_dims,...
                        'String',return_text,...
                        'FontSize',8.5,...
                        'FitBoxToText','on', ...
                        'BackgroundColor','w');
                else
                    returnIP_sign = 1;
                end
                if phaseWidth1 < 100
                    switch polarity
                        case 1
                            annotDims1 = [0.145 0.71 0.1 0.1];
                            annotDims2 = [0.77 0.71 0.1 0.1];
                        case -1
                            annotDims1 = [0.145 0.29 0.1 0.1];
                            annotDims2 = [0.77 0.29 0.1 0.1];

                    end
                else
                    switch polarity
                        case 1
                            annotDims1 = [0.145 0.71 0.1 0.1];
                            annotDims2 = [0.77 0.71 0.1 0.1];
                        case -1
                            annotDims1 = [0.145 0.31 0.1 0.1];
                            annotDims2 = [0.77 0.31 0.1 0.1];
                    end
                end
                annotFontSize1 = annotFontSize;
                if any(capacitance)
                    annotFontSize1 = annotFontSize - 0.5;
                end

                % phase 2
                drivingVoltage2_text = sprintf(...
                    '{\\itV}_{d,2} = %.3f V', ...
                    drivingVoltage2); % Vdrive text
                if hasDischargeDelay
                    accessVoltage4_text = sprintf(...
                        '{\\itV}_{at,2} = %.3f V', ...
                        accessVoltage4);    % Vacc text
                    accessResistance4_text = sprintf(...
                        '{\\itR}_{at,2} = %.1f k\\Omega', ...
                        accessResistance4);
                end
                if hasInterphaseDelay
                    accessVoltage3_text = sprintf(...
                        '{\\itV}_{al,2} = %.3f V', ...
                        accessVoltage3);    % Vacc text
                    accessResistance3_text = sprintf(...
                        '{\\itR}_{al,2} = %.1f k\\Omega', ...
                        accessResistance3);
                    if hasDischargeDelay
                        annot2_text_part = { ...
                            drivingVoltage2_text, ...
                            accessVoltage3_text, ...
                            accessVoltage4_text,...
                            accessResistance3_text, ...
                            accessResistance4_text};
                    else
                        annot2_text_part = { ...
                            drivingVoltage2_text, ...
                            accessVoltage3_text, ...
                            accessResistance3_text};
                    end
                else
                    if hasDischargeDelay
                        annot2_text_part = { ...
                            drivingVoltage2_text, ...
                            accessVoltage4_text,...
                            accessResistance4_text};
                    else
                        annot2_text_part = { ...
                            drivingVoltage2_text};
                    end
                end
                if hasDischargeDelay
                    potentialExcursion2_text = sprintf(...
                        '{\\itE}_{m%s}(%s) = %.3f V',...
                        electrodePol2_sign,polType,potentialExcursion2);
                        annot2_text = [ ...
                            potentialExcursion2_text ...
                            annot2_text_part];
                    if isReturnFound %&& (isBP || isTP)
                        returnExcursion2_text = sprintf(...
                            '{\\itE}{\\bf''}_{m%s}(%s) = %.3f V',...
                            electrodePol1_sign,polType,returnExcursion2);     % Ema text
                        annot2_text = [ ...
                            potentialExcursion2_text ...
                            returnExcursion2_text ...
                            annot2_text_part];
                    end
                    
                else
                    annot2_text = annot2_text_part;
                end

                % Scatter
                figure(groupNum);
                % access
                if hasInterphaseDelay
                    if hasDischargeDelay
                        access_time = [ ...
                            accessVoltage1_time accessVoltage2_time, ...
                            accessVoltage3_time accessVoltage4_time];
                        access_voltage = [ ...
                            accessVoltage1_plot accessVoltage2_plot, ...
                            accessVoltage3_plot accessVoltage4_plot];
                    else
                        access_time = [ ...
                            accessVoltage1_time ...
                            accessVoltage2_time, ...
                            accessVoltage3_time];
                        access_voltage = [ ...
                            accessVoltage1_plot ...
                            accessVoltage2_plot, ...
                            accessVoltage3_plot];
                    end
                else
                    if hasDischargeDelay
                        access_time = [accessVoltage1_time accessVoltage4_time];
                        access_voltage = [accessVoltage1_plot accessVoltage4_plot];
                    else
                        access_time = accessVoltage1_time;
                        access_voltage = accessVoltage1_plot;
                    end
                end
                scatter(access_time,access_voltage,...
                    'Marker','+',...
                    'SizeData',PLUS_SIZE,...
                    'LineWidth',SCATTER_WIDTH,...
                    'MarkerEdgeColor',SCATTER_COLOR,...
                    'HandleVisibility','off');

                % driving
                driving_time = [ ...
                    drivingVoltage1_time ...
                    drivingVoltage2_time ...
                    drivingVoltage3_time];
                driving_voltage = [ ...
                    drivingVoltage1_plot ...
                    drivingVoltage2_plot ...
                    drivingVoltage3_plot];
                % potential excursion
                scatter(driving_time,driving_voltage,...
                    'Marker','_',...
                    'SizeData',HORZ_SIZE,...
                    'LineWidth',SCATTER_WIDTH,...
                    'MarkerEdgeColor',SCATTER_COLOR,...
                    'HandleVisibility','off');

                % potential excursion
                if isPolAtTime
                    if hasInterphaseDelay
                        scatter( ...
                            potentialExcursion1_time,potentialExcursion1,...
                            'Marker','+',...
                            'SizeData',PLUS_SIZE,...
                            'LineWidth',SCATTER_WIDTH,...
                            'MarkerEdgeColor',SCATTER_COLOR,...
                            'HandleVisibility','off');
                        if isReturnFound %&& (isBP || isTP)
                            scatter( ...
                                potentialExcursion1_time,returnExcursion1,...
                            'Marker','+',...
                            'SizeData',PLUS_SIZE+5,...
                            'LineWidth',SCATTER_WIDTH,...
                            'MarkerEdgeColor',SCATTER_COLOR,...
                            'HandleVisibility','off');
                        end
                        if isTriphasic
                            scatter( ...
                                potentialExcursion2_time,potentialExcursion2,...
                                'Marker','+',...
                                'SizeData',PLUS_SIZE,...
                                'LineWidth',SCATTER_WIDTH,...
                                'MarkerEdgeColor',SCATTER_COLOR,...
                                'HandleVisibility','off');
                            if isReturnFound %&& (isBP || isTP)
                                scatter(potentialExcursion2_time,returnExcursion2,...
                                    'Marker','+',...
                                    'SizeData',PLUS_SIZE+5,...
                                    'LineWidth',SCATTER_WIDTH,...
                                    'MarkerEdgeColor',SCATTER_COLOR,...
                                    'HandleVisibility','off');
                            end
                        end
                    end
                    if hasDischargeDelay
                        if isTriphasic
                            scatter( ...
                                potentialExcursion3_time,potentialExcursion3,...
                                'Marker','+',...
                                'SizeData',PLUS_SIZE,...
                                'LineWidth',SCATTER_WIDTH,...
                                'MarkerEdgeColor',SCATTER_COLOR,...
                                'HandleVisibility','off');
                            if isReturnFound %&& (isBP || isTP)
                                scatter( ...
                                    potentialExcursion3_time,returnExcursion3,...
                                    'Marker','+',...
                                    'SizeData',PLUS_SIZE+5,...
                                    'LineWidth',SCATTER_WIDTH,...
                                    'MarkerEdgeColor',SCATTER_COLOR,...
                                    'HandleVisibility','off');
                            end
                        else
                            scatter( ...
                                potentialExcursion2_time,potentialExcursion2,...
                                'Marker','+',...
                                'SizeData',PLUS_SIZE,...
                                'LineWidth',SCATTER_WIDTH,...
                                'MarkerEdgeColor',SCATTER_COLOR,...
                                'HandleVisibility','off');
                            if isReturnFound %&& (isBP || isTP)
                                scatter( ...
                                    potentialExcursion2_time,returnExcursion2,...
                                    'Marker','+',...
                                    'SizeData',PLUS_SIZE+5,...
                                    'LineWidth',SCATTER_WIDTH,...
                                    'MarkerEdgeColor',SCATTER_COLOR,...
                                    'HandleVisibility','off');
                            end
                        end
                    end
                    drawnow;
                end
            end
        else
            if ~isempty(capacitance)
                capacitance_round = round(capacitance,1);
                capacitance_use = addCommas(capacitance_round);
                capacitance_text = sprintf('{\\itC} = %s nF',capacitance_use); % C text
                try
                    annotation(...
                        fig,...
                        'textbox',annotDims1,...
                        'String',capacitance_text,...
                        'FontSize',FONT_SIZE,...
                        'FitBoxToText','on', ...
                        'BackgroundColor','w');
                drawnow;
                catch
                end
            end
        end

        %% Labels
        if isPulsing
            channelName = sprintf('Channel %d Pulsing',channelNum);
        else
            channelName = File.Data(groupNum).Name;
        end
        plotTitle = title(channelName);
        sub = subtitle(subtitle_charge);
        ax = gca;
        set(ax,'XColor','k');
        time_label = ['Time (' MU_SIGN 's)'];
        xlabel(time_label);
            if hasInterphaseDelay
                if isPolAtTime
                    pol1_time = potentialExcursion1_time;
                else
                    pol1_time = accessVoltage1_time;
                end
                pol1_line = xline(pol1_time,'k--','HandleVisibility','off');
                if isTriphasic
                    if isPolAtTime
                        pol2_time = potentialExcursion2_time;
                    else
                        pol2_time = accessVoltage4_time;
                    end
                    pol2_line = xline(pol2_time,'k--','HandleVisibility','off');
                end
            end
            if isTriphasic
                if hasDischargeDelay
                    if isPolAtTime
                        pol3_time = potentialExcursion3_time;
                    else
                        pol3_time = accessVoltage6_time;
                    end
                    pol3_line = xline(pol3_time,'k--','HandleVisibility','off');
                end
            else
                if hasDischargeDelay
                    if isPolAtTime
                        pol2_time = potentialExcursion2_time;
                    else
                        pol2_time = accessVoltage4_time;
                    end
                    pol2_line = xline(pol2_time,'k--','HandleVisibility','off');
                end
            end
        

        % numOfDigits = ceil(log10(max(time)));
        % scale = 10^(numOfDigits-1);
        % xMin_round = ceil(min(time));
        % xMax_round = floor(max(time));
        % xMin_rem = rem(xMin_round,scale);
        % xMax_rem = rem(xMax_round,scale);
        % xMin = xMin_round - xMin_rem;
        % xMax = xMax_round - xMax_rem;
        diffTime = mean(diff(time));
        xMin = round(min(time),2,'significant');
        xMax = round(max(time),2,'significant');
        xMin_fix = xMin - diffTime;
        xMax_fix = xMax + diffTime;
        xlim([xMin_fix xMax_fix]);
        if timeScale < 25
            xRange = xMin:timeScale:xMax;
            set(ax,'XTick',xRange);
        end
        set(fig,'Position',[figPosX,figPosY,FIG_WIDTH,FIG_HEIGHT]);
        % if isPTP
        %     set(ax,'FontSize',FONT_SIZE-2);
        % else
            set(ax,'FontSize',FONT_SIZE);
        % end
        sub.FontSize = subtitleFont;
        box on;
        if numOfFieldsUsed > 1
            if phaseWidth1 < 100
                legendFont = 7;
            else
                legendFont = 8;
            end
            % if returnIP < -0.100
            %     legendPosition = 'northeast';
            % else
            if numOfPulseWidth > 1
                if phaseWidth_ratio < 1
                switch polarity
                    case -1
                        legendPosition = 'northwest';
                    case 1
                        legendPosition = 'southwest';
                end
                else
                    switch polarity
                        case -1
                            legendPosition = 'northeast';
                        case 1
                            legendPosition = 'southeast';
                    end
                end
            elseif isPartial
                legendFont = 7;
                switch polarity
                    case -1
                        legendPosition = 'northeast';
                    case 1
                        legendPosition = 'southeast';
                end
            else
                switch polarity
                    case -1
                        legendPosition = 'northeast';
                    case 1
                        legendPosition = 'southeast';
                end
            end
            % end
        else
            legendFont = 9;
            switch polarity
                case -1
                    legendPosition = 'northwest';
                case 1
                    legendPosition = 'southwest';
            end
        end
        leg = legend;
        leg.Location = legendPosition;
        leg.FontSize = legendFont;
    end
end

% Annotation
spacing_before = '';
spacing_after = '';
% numOfSpaces = floor(200 / phaseWidth1) - 1;
% if numOfSpaces > 1
%     for idx = 1:numOfSpaces
%         spacing_alloc = [spacing ' '];
%         spacing = spacing_alloc;
%     end
% end
if phaseWidth1 < 100
    spacing_before = '  ';
    horzAlign = 'left';
    vertAlign1 = 'middle';
    vertAlign2 = 'middle';
    offset = 0.2;
else
    horzAlign = 'center';
    vertAlign1 = 'bottom';
    vertAlign2 = 'top';
    offset = 0.5;
end

limitVariable = '';
if isActiveFound
    limitVariable = 'E';
    lowerPotential = File.Parameters.ReferenceElectrode.LowerPotential;
    upperPotential = File.Parameters.ReferenceElectrode.UpperPotential;
elseif ~hasActive
    limitVariable = 'V';
    lowerPotential = File.Parameters.CounterElectrode.LowerPotential;
    upperPotential = File.Parameters.CounterElectrode.UpperPotential;
end
if ~isempty(limitVariable)
    if (hasInterphaseDelay || isTriphasic) ...
            &&  ylim_left_min < lowerPotential - offset
        limit1_label = sprintf('%s{\\it{%s}}_{lc} = %+.3f V%s', ...
        spacing_before,limitVariable,lowerPotential,spacing_after); %  
        limit1_line = yline(lowerPotential,'k--', ...
            limit1_label, ...
            'LabelHorizontalAlignment',horzAlign, ...
            'LabelVerticalAlignment',vertAlign1, ...
            'HandleVisibility','off');
    end
    if (hasDischargeDelay || isTriphasic) ...
            && ylim_left_max > upperPotential + offset
        limit2_label = sprintf('%s{\\it{%s}}_{la} = %+.3f V%s', ...
        spacing_before,limitVariable,upperPotential,spacing_after); %
        limit2_line = yline(upperPotential,'k--', ...
            limit2_label, ...
            'LabelHorizontalAlignment',horzAlign, ...
            'LabelVerticalAlignment',vertAlign2, ...
            'HandleVisibility','off');
    end
end

if ~isempty(annotDims1)
    firstAnnot = annotation(...
        fig,...
        'textbox',annotDims1,...
        'String',annot1_text,...
        'FontSize',annotFontSize1,...
        'FitBoxToText','on', ...
        'BackgroundColor','w');
end
if ~isempty(annotDims2)
    secondAnnot = annotation(...
        fig,...
        'textbox',annotDims2,...
        'String',annot2_text,...
        'FontSize',annotFontSize,...
        'FitBoxToText','on', ...
        'BackgroundColor','w');
end

%% Inset
if (isMP || isCG || isPartial) && isReturnFound
    insetPositionY_shift = 0;
    if ~isSymmetric
        if phaseWidth_ratio > 2
            insetScale = 0.2;
            insetPositionX = 0.52;
            switch polarity
                case -1
                    insetPositionY_shift = 0.26;
                case 1
                    insetPositionY_shift = -0.3;
            end
            insetFont = FONT_SIZE / 2.25;
        % elseif phaseWidth_ratio > 3
        %     insetScale = 0.2;
        %     insetPositionX = 0.66;
        %     switch polarity
        %         case -1
        %             insetPositionY_shift = 0.17;
        %         case 1
        %             insetPositionY_shift = -0.025;
        %     end
        %     insetFont = FONT_SIZE / 2.25;
        % elseif phaseWidth_ratio > 2
        %     insetScale = 0.2;
        %     insetPositionX = 0.665;
        %     switch polarity
        %         case -1
        %             insetPositionY_shift = 0.17;
        %         case 1
        %             insetPositionY_shift = -0.025;
        %     end
        %     insetFont = FONT_SIZE / 2.25;
        elseif phaseWidth_ratio > 1
            insetScale = 0.2;
            insetPositionX = 0.5;
            switch polarity
                case -1
                    insetPositionY_shift = 0.26;
                case 1
                    insetPositionY_shift = -0.025;
            end
            insetFont = FONT_SIZE / 2.25;
        else
            insetScale = 0.25;
            insetPositionX = 0.63;
            switch polarity
                case -1
                    insetPositionY_shift = 0.025;
                case 1
                 insetPositionY_shift = -0.025;
            end
            insetFont = FONT_SIZE / 2;
        end
    else
        if phaseWidth1 < 100
            insetScale = 0.2;
            insetPositionX = 0.29;
            switch polarity
                case -1
                    insetPositionY_shift = -0.025;
                case 1
            end
            insetFont = FONT_SIZE / 2.25;
        else
            insetScale = 0.2;
            insetPositionY_shift = -0.025;
            switch polarity
                case -1
                    insetPositionX = 0.18;
                case 1
                    insetPositionX = 0.18;
            end
            insetFont = FONT_SIZE / 2.25;
        end
    end
    switch polarity
        case -1
            insetDims = [insetPositionX 0.57-insetPositionY_shift ...
                insetScale insetScale];
        case 1
            insetDims = [insetPositionX 0.18-insetPositionY_shift ...
                insetScale insetScale];
    end
    insetAx = axes('Position',insetDims);
    cla;
    return_arr = File.Data(groupNum).Capture(captureNum).(returnField_name);
    plot(time,return_arr,...
        'LineStyle','-',...
        'Marker','none', ...
        'Color',returnColor,...
        'LineWidth',LINE_WIDTH, ...
        'HandleVisibility','off');
    % linkaxes([ax insetAx], 'xy');
    title(insetAx,'','Color','w');
    % time_min = min(time);
    % if -interphaseDelay < time_min
    %     insetTimeStart = time_min;
    % else
    %     insetTimeStart = -interphaseDelay;
    % end
    % xlim([insetTimeStart pulseWidth]);
    xlim([xMin_fix xMax_fix]);
    if range(return_arr) < 0.2
        insetAx.YAxis.Exponent = -3;
    end
    insetAx.FontSize = insetFont;
    ylim('tickaligned');
    try
        insetAx.YAxis(2).Visible = 'off';
    catch
    end
    box on;
    xtickformat('%,g');
    % ytickformat('%d');
end

captureNum_use = sprintf('Number of Captures: %d',captureNum);
captureAnnot = annotation(...
    fig,...
    'textbox',[0.015 0.05 0.01 0.01],...
    'String',captureNum_use,...
    'FontSize',8,...
    'FitBoxToText','on');
drawnow;

% if captureNum > 1
%     figure(numOfGroups+groupNum);
%     plot(currentStimList,excursionBothList, ...
%         'LineStyle','none', ...
%         'Marker','+'); hold on;
%     xlabel('Amplitude Magnitude (\\muA)');
%     ylabel('Maximum Potential Excursion (V)');
%     fit1 = File.Data(groupNum).Fitting.Fit1;
%     yfit1 = polyval(fit1,currentStim_range);
%     plot(currentStim_range,yfit1,'k--')
%     if hasDischargeDelay
%         fit2 = File.Data(groupNum).Fitting.Fit2;
%         yfit2 = polyval(fit2,currentStim_range);
%         plot(currentStim_range,yfit2,'k--');
% 
%     end
%     hold off;
%     legend(legend_cell);
%     set(fig,'Position',[screenWidth,figPosY-FIG_HEIGHT-WINDOW_HEADER,FIG_WIDTH,FIG_HEIGHT]);
% end

varargout{1} = buttonHandle;
varargout{2} = ax;

[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end

