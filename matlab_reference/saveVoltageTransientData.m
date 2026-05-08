function varargout = saveVoltageTransientData(File)
%% Variables
fprintf('Setting up files...');
startTime = tic;

% Experiment
expID = File.Test.Experiment;
isTriphasic = contains2(expID,'TV');
isLongPulsing = contains2(expID,'LP');
fileName = File.Name;
folderPath = File.Path;

% Oscilloscope
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
hasAltActive = isVoltageSpecial && hasReturnChannel;

% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelID_cell = {File.Data(:).ID};
empty_tf = cellfun(@isempty,channelID_cell);
channelID_cell(empty_tf) = [];
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);

% Captures
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);
Capture = File.Data(groupNum).Capture(captureNum);

% Fields
field_cell = fieldnames(Capture);
timeField_idx = find(containsi(field_cell,'Time'));
currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
dataFields_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
numOfDataFields = length(dataFields_cell);
empty_tf = zeros(numOfDataFields,1);
fieldsUsed_cell = dataFields_cell;
for dataFieldNum = 1:numOfDataFields
    field = dataFields_cell{dataFieldNum};
    try
        data = File.Data(groupNum).Capture(captureNum).(field);
    catch
        empty_tf(dataFieldNum) = true;
    end
    if isempty(data)
        empty_tf(dataFieldNum) = true;
    end
end
empty_tf = flip(find(empty_tf));
if ~isempty(empty_tf)
    fieldsUsed_cell(empty_tf) = [];
end
numOfFieldsUsed = length(fieldsUsed_cell);

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isCG = contains2(configID,'CG');

% Pattern
polarity = File.Parameters.Polarity;
interphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;

% Electrode
activeElectrode = File.Parameters.WorkingElectrode.Type;
refElectrode = File.Parameters.ReferenceElectrode.Type;
returnElectrode = File.Parameters.CounterElectrode.Type;

% Initialize Arrays
if ~isVoltageSpecial || hasAltActive
    numOfColumns = 13 + numOfFieldsUsed;
else
    numOfColumns = 12 + numOfFieldsUsed;
end
cell_arr = cell(dataLength,1);	% array of blank space
tableHeading = cell(1,numOfColumns);
table_cell = cell(dataLength,numOfColumns);
activeExcursion_cell = cell_arr;
returnExcursion_cell = cell_arr;
accessVoltage_cell = cell_arr;
drivingVoltage_cell = cell_arr;
effectiveCapacitance_cell = cell_arr;
accessResistance_cell = cell_arr;
chargingCapacitance_cell = cell_arr;
drivingPotential_cell = cell_arr;

zero_arr = zeros(1,groupNum);
amplitude_arr = zero_arr;
chargePhase_arr = zero_arr;
chargeInjection_arr = zero_arr;
activeExcursion1_arr = zero_arr;
activeExcursion2_arr = zero_arr;
returnExcursion1_arr = zero_arr;
returnExcursion2_arr = zero_arr;
drivingVoltage1_arr = zero_arr;
drivingVoltage2_arr = zero_arr;
effectiveCapacitance_arr = zero_arr;
accessVoltage1_arr = zero_arr;
accessVoltage2_arr = zero_arr;
accessVoltage3_arr = zero_arr;
accessVoltage4_arr = zero_arr;
accessResistance1_arr = zero_arr;
accessResistance2_arr = zero_arr;
accessResistance3_arr = zero_arr;
accessResistance4_arr = zero_arr;
chargingCapacitance_arr = zero_arr;
activeDriving1_arr = zero_arr;
activeDriving2_arr = zero_arr;
returnDriving1_arr = zero_arr;
returnDriving2_arr = zero_arr;
if isTriphasic
    activeExcursion3_arr = zero_arr;
    returnExcursion3_arr = zero_arr;
    drivingVoltage3_arr = zero_arr;
    accessVoltage5_arr = zero_arr;
    accessVoltage6_arr = zero_arr;
    accessResistance5_arr = zero_arr;
    accessResistance6_arr = zero_arr;
    activeDriving3_arr = zero_arr;
    returnDriving3_arr = zero_arr;
end
%% File Names
% .mat fileName
isSaveMAT = ~isLongPulsing;
if isSaveMAT
    fileName_mat = [fileName '.mat'];       	% fileName for .mat file
    filePath_mat = fullfile(folderPath,fileName_mat);  % save path for .mat file
    tempName_mat = [fileName '_temp.mat'];       	% fileName for .mat file
    tempPath_mat = fullfile(folderPath,tempName_mat);  % save path for .mat file
end

% .xlsx fileName
isSaveXLSX = true;
hasAutoWidth = numOfGroups <= 16;
if isSaveXLSX
    fileName_xlsx = [fileName '.xlsx'];           % fileName for .xlsx file
    filePath_xlsx = fullfile(folderPath,fileName_xlsx); 	% save path for .xlsx file
    tempName_xlsx = [fileName '_temp.xlsx'];       	% fileName for .mat file
    tempPath_xlsx = fullfile(folderPath,tempName_xlsx);  % save path for .mat file
    while true
        try
            if isfile(filePath_xlsx) && groupNum == 1
                delete(filePath_xlsx);
                updateWaitbar(File);
            end
            if groupNum == 1
                writetable(table(),filePath_xlsx, ...
                    'UseExcel',true, ...
                    'FileType','spreadsheet', ...
                    'Sheet','Values');    % save sheet to .xlsx file
                updateWaitbar(File);
            end
            break;
        catch
        end
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',fileName,endTime,unit);

%% Close Files
fclose('all');
fprintf('\t');
File = setScopeStatus(File,'close');
File = rmfield(File,'Oscilloscope');

%% Save
% Making .mat file
if isSaveMAT
    fprintf('\tSaving MATLAB file...');
    startTime = tic;
    if groupNum == numOfGroups
        File.Oscilloscope.Object = [];
    end
    varName = getVarName(File);
    newVarName = ['File_' fileName];
    setNewVarName = sprintf('%s = %s;',newVarName,varName);
    eval(setNewVarName);
    while true
        try
            try
                save(filePath_mat,newVarName);             	% save .mat file
                updateWaitbar(File);
            catch
                save(tempPath_mat,newVarName);             	% save .mat file
                delete(filePath_mat);
                movefile(tempPath_mat,filePath_mat,'f');
                updateWaitbar(File);
            end
            break;
        catch
        end
    end
    varargout{1} = filePath_mat;
    [endTime,unit] = getEndTime(startTime);
    fprintf('"%s" (%.2f %s)\n',fileName_mat,endTime,unit);         	% .mat file saved
end

% Making spreadsheet files
fprintf('\tSaving Excel file...');
startTime = tic;
for group_idx = 1:groupNum % loop through channel data
    % Capture data of channel
    capture_arr = [File.Data(group_idx).Capture(:).Index];
    numOfCaptures = length(capture_arr);
    captureNum = capture_arr(numOfCaptures);
    amplitude = File.Data(group_idx).Capture(captureNum).Amplitude;      	% amplitude value
    if amplitude == 0 || isnan(amplitude)
        amplitude = NaN;
        chargePhase = NaN;
        chargeInjection = NaN;
    else
        chargePhase = File.Data(group_idx).Capture(captureNum).ChargePhase;      	% charge per phase value
        chargeInjection = File.Data(group_idx).Capture(captureNum).ChargeInjection;           % charge injection value
    end
    activeExcursion = File.Data(group_idx).Capture(captureNum).PotentialExcursion;   % maximum potential value
    if hasReturnChannel
        returnExcursion = File.Data(group_idx).Capture(captureNum).ReturnExcursion;   % maximum cathodal potential value
    end
    drivingVoltage = File.Data(group_idx).Capture(captureNum).DrivingVoltage;           % driving voltage value
    effectiveCapacitance = File.Data(group_idx).Capture(captureNum).EffectiveCapacitance;
    accessVoltage = File.Data(group_idx).Capture(captureNum).AccessVoltage;
    accessResistance = File.Data(group_idx).Capture(captureNum).AccessResistance;
    chargingCapacitance = File.Data(group_idx).Capture(captureNum).ChargingCapacitance;
    if ~isVoltageSpecial || hasAltActive
        drivingPotential = File.Data(group_idx).Capture(captureNum).DrivingPotential;
    end
    isGood = File.Data(group_idx).Capture(captureNum).Status.Good;
    try
        isAtMaxCurrent = File.Data(group_idx).Capture(captureNum).Status.MaxCurrent;
    catch
        isAtMaxCurrent = false;
    end

    % Multiple Values
    % potential excursion
    if hasInterphaseDelay
        if isTriphasic
            numOfExcursions = 2;
        else
            numOfExcursions = 1;
        end
    else
        numOfExcursions = 1;
    end
    if hasDischargeDelay
        numOfExcursions = numOfExcursions + 1;
    end
    
    for idx = 1:numOfExcursions
        activeExcursion_cell{idx} = activeExcursion(idx);
        if hasReturnChannel
            returnExcursion_cell{idx} = returnExcursion(idx);
        end
    end

    % driving voltage
    if isTriphasic
        numOfDriving = 3;
    else
        numOfDriving = 2;
    end
    for idx = 1:numOfDriving
        drivingVoltage_cell{idx} = drivingVoltage(idx);
    end

    % effective capacitance
    effectiveCapacitance_cell{1} = effectiveCapacitance(idx);

    % access
    if isTriphasic
        numOfAccess = 6;
    else
        numOfAccess = 4;
    end
    if hasInterphaseDelay
        if hasDischargeDelay
            idx_arr = 1:numOfAccess;
        else
            idx_arr = 1:numOfAccess-1;
        end
    else
        if hasDischargeDelay
            idx_arr = [1 numOfAccess];
        else
            idx_arr = 1;
        end
    end
    for idx = idx_arr
        accessVoltage_cell{idx} = accessVoltage(idx);
        accessResistance_cell{idx} = accessResistance(idx);
    end

    % chargingCapacitance
    chargingCapacitance_cell{1} = chargingCapacitive(idx);

    % driving potential
    if ~isVoltageSpecial || hasAltActive
        for idx = 1:numOfDriving
            drivingPotential_cell{idx} = drivingPotential(idx);
        end
    end
    if hasReturnChannel
        for idx = (1:numOfDriving) + numOfDriving
            drivingPotential_cell{idx} = drivingPotential(idx);
        end
    end

    % Potential Excursion Unit
    if hasReturnChannel
        switch polarity
            case -1
                activeExcursion1_label = 'Active Emc (V)';
                activeExcursion2_label = 'Active Ema (V)';
            case 1
                activeExcursion1_label = 'Active Ema (V)';
                activeExcursion2_label = 'Active Emc (V)';
        end
        if isTriphasic
            switch polarity
                case -1
                    activeExcursion3_label = 'Active Emc2 (V)';
                case 1
                    activeExcursion3_label = 'Active Ema2 (V)';
            end
        end
        switch -polarity
            case -1
                returnExcursion1_label = 'Return Emc (V)';
                returnExcursion2_label = 'Return Ema (V)';
            case 1
                returnExcursion1_label = 'Return Ema (V)';
                returnExcursion2_label = 'Return Emc (V)';
        end
        if isTriphasic
            switch -polarity
                case -1
                    returnExcursion3_label = 'Return Emc2 (V)';
                case 1
                    returnExcursion3_label = 'Return Ema2 (V)';
            end
        end
    else
        switch polarity
            case -1
                activeExcursion1_label = 'Emc (V)';
                activeExcursion2_label = 'Ema (V)';
            case 1
                activeExcursion1_label = 'Ema (V)';
                activeExcursion2_label = 'Emc (V)';
        end
        if isTriphasic
            switch polarity
                case -1
                    activeExcursion3_label = 'Emc2 (V)';
                case 1
                    activeExcursion3_label = 'Ema2 (V)';
            end
        end
    end

    % Compile
    if isMP || isCG
        if isGood
            channelSheet = sprintf('%d',group_idx);     % channel number (char)
            channelName = channelSheet;
        elseif isAtMaxCurrent
            channelSheet = sprintf('%d_MAX',group_idx);     % channel number (char)
            channelName = sprintf('%d (MAX)',group_idx);     % channel number (char)
        else
            channelSheet = sprintf('%d_BAD',group_idx);     % channel number (char)
            channelName = sprintf('%d (BAD)',group_idx);     % channel number (char)
        end
    else
        channelD = File.Data(groupNum).ID;
        if isGood
            channelSheet = channelD;     % channel number (char)
            channelName = channelSheet;
        elseif isAtMaxCurrent
            channelSheet = sprintf('%s_MAX',channelD);     % channel number (char)
            channelName = sprintf('%s (MAX)',channelD);     % channel number (char)
        else
            channelSheet = sprintf('%s_BAD',channelD);     % channel number (char)
            channelName = sprintf('%s (BAD)',channelD);     % channel number (char)
        end
    end
    amplitude_arr(group_idx) = amplitude;
    chargePhase_arr(group_idx) = chargePhase;
    chargeInjection_arr(group_idx) = chargeInjection;

    % Potential Excursion
    activeExcursion1_arr(group_idx) = activeExcursion(1);
    if isTriphasic
        activeExcursion2_arr(group_idx) = activeExcursion(2);
        if hasDischargeDelay
            activeExcursion3_arr(group_idx) = activeExcursion(3);
        end
    else
        if hasDischargeDelay
            activeExcursion2_arr(group_idx) = activeExcursion(2);
        end
    end
    if hasReturnChannel
        returnExcursion1_arr(group_idx) = returnExcursion(1);
        if isTriphasic
            returnExcursion2_arr(group_idx) = returnExcursion(2);
            if hasDischargeDelay
                returnExcursion3_arr(group_idx) = returnExcursion(3);
            end
        else
            if hasDischargeDelay
                returnExcursion2_arr(group_idx) = returnExcursion(2);
            end
        end
    end

    % Driving Voltage
    drivingVoltage1_arr(group_idx) = drivingVoltage(1);
    drivingVoltage2_arr(group_idx) = drivingVoltage(2);
    if isTriphasic
        drivingVoltage3_arr(group_idx) = drivingVoltage(3);
    end

    % Effective Capacitance
    effectiveCapacitance_arr(group_idx) = effectiveCapacitance;

    % Access
    accessVoltage1_arr(group_idx) = accessVoltage(1);
    accessResistance1_arr(group_idx) = accessResistance(1);
    if hasInterphaseDelay
        accessVoltage2_arr(group_idx) = accessVoltage(2);
        accessVoltage3_arr(group_idx) = accessVoltage(3);
        accessResistance2_arr(group_idx) = accessVoltage(2);
        accessResistance3_arr(group_idx) = accessResistance(3);
        if isTriphasic
            accessVoltage4_arr(group_idx) = accessVoltage(4);
            accessResistance4_arr(group_idx) = accessResistance(4);
            accessVoltage5_arr(group_idx) = accessVoltage(5);
            accessResistance5_arr(group_idx) = accessResistance(5);
        end
    end
    if isTriphasic
        if hasDischargeDelay
            accessVoltage6_arr(group_idx) = accessVoltage(6);
            accessResistance6_arr(group_idx) = accessResistance(6);
        end
    else
        if hasDischargeDelay
            accessVoltage4_arr(group_idx) = accessVoltage(4);
            accessResistance4_arr(group_idx) = accessResistance(4);
        end
    end

    % Effective Capacitance
    chargingCapacitance_arr(group_idx) = chargingCapacitance;

    % Driving Potential
    if ~isVoltageSpecial || hasAltActive
        activeDriving1_arr(group_idx) = drivingPotential(1);
        activeDriving2_arr(group_idx) = drivingPotential(2);
        if isTriphasic
            activeDriving3_arr(group_idx) = drivingPotential(3);
        end
    end
    if hasReturnChannel
        if isTriphasic
            returnDriving1_arr(group_idx) = drivingPotential(4);
            returnDriving2_arr(group_idx) = drivingPotential(5);
            returnDriving3_arr(group_idx) = drivingPotential(6);
        else
            returnDriving1_arr(group_idx) = drivingPotential(3);
            returnDriving2_arr(group_idx) = drivingPotential(4);
        end
    end

    % Save sheet
    if group_idx == groupNum
        time = File.Data(group_idx).Capture(captureNum).Time;              % time data array
        table_cell(:,1) = num2cell(time);
        for dataFieldNum = 1:numOfFieldsUsed
            field = fieldsUsed_cell{dataFieldNum};
            data = File.Data(group_idx).Capture(captureNum).(field);
            table_cell(:,dataFieldNum+1) = num2cell(data);
        end
        currentDensity = File.Data(group_idx).Capture(captureNum).CurrentDensity;
        surfaceArea = File.Data(group_idx).SurfaceArea;                   % geometric surface area
        dateTime = File.Data(group_idx).Capture(captureNum).DateTime;                   % date and time completed
        status = File.Data(group_idx).Capture(captureNum).Status.Description;
        try
            table_cell(:,numOfFieldsUsed+2) = num2cell(currentDensity);
        catch
            current = File.Data(group_idx).Capture(captureNum).Current;
            table_cell(:,numOfFieldsUsed+2) = getCurrentDensity(current,surfaceArea,'uA','A');
        end

        % Table
        tableHeading{1} = 'Time (us)';
        for dataFieldNum = 1:numOfFieldsUsed
            field = fieldsUsed_cell{dataFieldNum};
            if contains2(field,'curr')
                channelName = [field ' (uA)'];
            else
                if contains2(field,{'act','work','cou','ret','diff','pot'})
                    channelName = sprintf('%s (%s versus %s, V)', ...
                        field,activeElectrode,refElectrode);
                elseif contains2(field,{'cou','ret'})
                    channelName = sprintf('%s (%s versus %s, V)', ...
                        field,returnElectrode,refElectrode);
                elseif contains2(field,{'volt'})
                    channelName = sprintf('%s (%s versus %s, V)', ...
                        field,activeElectrode,returnElectrode);
                end
            end
            tableHeading{dataFieldNum + 1} = channelName;
        end
        tableHeading{numOfFieldsUsed+2} = 'Current Density (A/cm2)';
        tableHeading{numOfFieldsUsed+3} = 'Amplitude (us)';
        table_cell{1,numOfFieldsUsed+3} = amplitude;
        tableHeading{numOfFieldsUsed+4} = 'Qph (nC/ph)';
        table_cell{1,numOfFieldsUsed+4} = chargePhase;
        tableHeading{numOfFieldsUsed+5} = 'Area (um2)';
        table_cell{1,numOfFieldsUsed+5} = surfaceArea;
        tableHeading{numOfFieldsUsed+6} = 'Qinj (mC/cm2)';
        table_cell{1,numOfFieldsUsed+6} = chargeInjection;

        idx_add = 7;
        tableHeading{numOfFieldsUsed+idx_add} = 'Epola (V)';
        for idx = 1:numOfExcursions
            table_cell{idx,numOfFieldsUsed+idx_add} = activeExcursion_cell{idx};
        end

        if hasReturnChannel
            idx_add = idx_add + 1;
            tableHeading{numOfFieldsUsed+idx_add} = 'Epolr (V)';
            for idx = 1:numOfExcursions
                table_cell{idx,numOfFieldsUsed+idx_add} = returnExcursion_cell{idx};
            end
        end

        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Vd (V)';    % driving voltage
        for idx = 1:numOfDriving
            table_cell{idx,numOfFieldsUsed+idx_add} = drivingVoltage_cell{idx};
        end

        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Ceff (mF/cm2)';    % effective capacitance
        table_cell{idx,numOfFieldsUsed+idx_add} = effectiveCapacitance_cell{idx};

        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Va (V)';
        for idx = idx_arr
            table_cell{idx,numOfFieldsUsed+idx_add} = abs(accessVoltage_cell{idx});
        end

        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Ra (kOhm)';
        for idx = idx_arr
            table_cell{idx,numOfFieldsUsed+idx_add} = accessResistance_cell{idx};
        end
        
        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Cch (nF)';    % effective capacitance
        table_cell{idx,numOfFieldsUsed+idx_add} = chargingCapacitance_cell{idx};

        if ~isVoltageSpecial || hasAltActive
            idx_add = idx_add + 1;
            tableHeading{numOfFieldsUsed+idx_add} = 'Eda (V)';
            for idx = 1:numOfDriving
                table_cell{idx,numOfFieldsUsed+idx_add} = drivingPotential_cell{idx};
            end

            if hasReturnChannel
                idx_add = idx_add + 1;
                tableHeading{numOfFieldsUsed+idx_add} = 'Edr (V)';
                idx_shifted = idx + numOfDriving;
                table_cell{idx_shifted,numOfFieldsUsed+idx_add} = drivingPotential_cell{idx_shifted};
            end
        end
        
        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Date Time'; % date and time
        table_cell{1,numOfFieldsUsed+idx_add} = dateTime;

        idx_add = idx_add + 1;
        tableHeading{numOfFieldsUsed+idx_add} = 'Status';
        table_cell{1,numOfFieldsUsed+idx_add} = status;

        % Table
        tableData = cell2table(table_cell, ...
            'VariableNames',tableHeading);

        % Save
        while true
            try
                [status,values] = fileattrib(filePath_xlsx);
                if status
                    isWriteable = values.UserWrite;
                    if ~isWriteable
                        copyfile(filePath_xlsx,tempPath_xlsx,'f');
                        xlsxPath = tempPath_xlsx;
                    else
                        xlsxPath = filePath_xlsx;
                    end
                else
                    isWriteable = true;
                    xlsxPath = filePath_xlsx;
                end
                updateWaitbar(File,['Saving ' channelSheet '...']);
                writetable(tableData,xlsxPath, ...
                    'FileType','spreadsheet', ...
                    'UseExcel',true, ...
                    'AutoFitWidth',hasAutoWidth, ...
                    'Sheet',channelSheet);    % save sheet to .xlsx file
                if ~isWriteable
                    delete(filePath_xlsx);
                    movefile(tempPath_xlsx,filePath_xlsx,'f');
                    updateWaitbar(File);
                end
                break;
            catch
            end
        end
        channelName = File.Data(group_idx).Name;
        fprintf('%s...',channelName);
    end
end

% Compiled Value Sheet
% stimulation
stim_arr = [ ...
    amplitude_arr; ...
    chargePhase_arr; ...
    chargeInjection_arr];
stimHeadings_cell = {
    'Istim (uA)', ...
    'Qph (nC/ph)', ...   % charge per phase
    'Qinj (mC/cm2)'}; % charge injection

% excursion
excursion_arr = activeExcursion1_arr;
excursionHeadings = {activeExcursion1_label};
if isTriphasic
    if hasInterphaseDelay
        excursion_arr = [excursion_arr;activeExcursion2_arr];
        excursionHeadings = [excursionHeadings,activeExcursion2_label];
    end
    if hasDischargeDelay
        excursion_arr = [excursion_arr;activeExcursion3_arr];
        excursionHeadings = [excursionHeadings,activeExcursion3_label];
    end
else
    if hasDischargeDelay
        excursion_arr = [excursion_arr;activeExcursion2_arr];
        excursionHeadings = [excursionHeadings,activeExcursion2_label];
    end
end
if hasReturnChannel
    excursion_arr = [ ...
        excursion_arr; ...
        returnExcursion1_arr];
    excursionHeadings = [ ...
        excursionHeadings, ...
        returnExcursion1_label];
    if isTriphasic
        if hasInterphaseDelay
            excursion_arr = [ ...
                excursion_arr; ...
                returnExcursion2_arr];
            excursionHeadings = [ ...
                excursionHeadings, ...
                returnExcursion2_label];
        end
        if hasDischargeDelay
            excursion_arr = [ ...
                excursion_arr; ...
                returnExcursion3_arr];
            excursionHeadings = [ ...
                excursionHeadings, ...
                returnExcursion3_label];
        end
    else
        if hasDischargeDelay
            excursion_arr = [ ...
                excursion_arr; ...
                returnExcursion2_arr];
            excursionHeadings = [ ...
                excursionHeadings, ...
                returnExcursion2_label];
        end
    end
end

% driving
driving_arr = [drivingVoltage1_arr;drivingVoltage2_arr];
drivingHeadings_cell = {'Vd1 (V)','Vd2 (V)'};
if isTriphasic
    driving_arr = [driving_arr;drivingVoltage3_arr];
    drivingHeadings_cell = [drivingHeadings_cell,'Vd3 (V)'];
end

% access
if isTriphasic
    if hasInterphaseDelay
        if hasDischargeDelay
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessVoltage2_arr; ...
                accessVoltage3_arr; ...
                accessVoltage4_arr; ...
                accessVoltage5_arr; ...
                accessVoltage6_arr; ...
                accessResistance1_arr; ...
                accessResistance2_arr; ...
                accessResistance3_arr; ...
                accessResistance4_arr; ...
                accessResistance5_arr; ...
                accessResistance6_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Vat1 (V)', ...
                'Val2 (V)', ...
                'Vat2 (V)', ...
                'Val3 (V)', ...
                'Vat3 (V)', ...
                'Ral1 (kOhm)', ...
                'Rat1 (kOhm)', ...
                'Ral2 (kOhm)', ...
                'Rat2 (kOhm)', ...
                'Ral3 (kOhm)', ...
                'Rat3 (kOhm)'};
        else
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessVoltage2_arr; ...
                accessVoltage3_arr; ...
                accessVoltage4_arr; ...
                accessVoltage5_arr; ...
                accessResistance1_arr; ...
                accessResistance2_arr; ...
                accessResistance3_arr; ...
                accessResistance4_arr; ...
                accessResistance5_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Vat1 (V)', ...
                'Val2 (V)', ...
                'Vat2 (V)', ...
                'Val3 (V)', ...
                'Ral1 (kOhm)', ...
                'Rat1 (kOhm)', ...
                'Ral2 (kOhm)', ...
                'Rat2 (kOhm)', ...
                'Ral3 (kOhm)'};
        end
    else
        if hasDischargeDelay
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessVoltage6_arr; ...
                accessResistance1_arr; ...
                accessResistance6_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Vat3 (V)', ...
                'Ral1 (kOhm)', ...
                'Rat3 (kOhm)'};
        else
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessResistance1_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Ral1 (kOhm)'};
        end
    end
else
    if hasInterphaseDelay
        if hasDischargeDelay
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessVoltage2_arr; ...
                accessVoltage3_arr; ...
                accessVoltage4_arr; ...
                accessResistance1_arr; ...
                accessResistance2_arr; ...
                accessResistance3_arr; ...
                accessResistance4_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Vat1 (V)', ...
                'Val2 (V)', ...
                'Vat2 (V)', ...
                'Ral1 (kOhm)', ...
                'Rat1 (kOhm)', ...
                'Ral2 (kOhm)', ...
                'Rat2 (kOhm)'};
        else
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessVoltage2_arr; ...
                accessVoltage3_arr; ...
                accessResistance1_arr; ...
                accessResistance2_arr; ...
                accessResistance3_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Vat1 (V)', ...
                'Val2 (V)', ...
                'Ral1 (kOhm)', ...
                'Rat1 (kOhm)', ...
                'Ral2 (kOhm)'};
        end
    else
        if hasDischargeDelay
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessVoltage4_arr; ...
                accessResistance1_arr; ...
                accessResistance4_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Vat2 (V)', ...
                'Ral1 (kOhm)', ...
                'Rat2 (kOhm)'};
        else
            access_arr = [ ...
                accessVoltage1_arr; ...
                accessResistance1_arr];
            accessHeadings_cell = { ...
                'Val1 (V)', ...
                'Ral1 (kOhm)'};
        end
    end
end
access_arr = abs(access_arr);

% data
data_arr = [ ...
    stim_arr; ...
    excursion_arr; ...
    driving_arr; ...
    effectiveCapacitance_arr; ...
    access_arr; ...
    chargingCapacitance_arr];

% headings
rowHeadings_cell = [ ...
    stimHeadings_cell, ...
    excursionHeadings, ...
    drivingHeadings_cell, ...
    'Ceff (mF/cm2)', ...
    accessHeadings_cell, ...
    'Cch (nF)'];

% active driving potential
if ~isVoltageSpecial || hasAltActive
    data_arr = [data_arr; ...
        activeDriving1_arr; ...
        activeDriving2_arr];
    rowHeadings_cell = [rowHeadings_cell, ...
        'Eda1 (V)','Eda2 (V)'];
    if isTriphasic
        data_arr = [data_arr;activeDriving3_arr];
        rowHeadings_cell = [rowHeadings_cell,'Eda3 (V)'];
    end
end

% return driving potential
if hasReturnChannel
    data_arr = [data_arr; ...
        returnDriving1_arr; ...
        returnDriving2_arr];
    rowHeadings_cell = [rowHeadings_cell, ...
        'Edr1 (V)','Edr2 (V)'];
    if isTriphasic
        data_arr = [data_arr;returnDriving3_arr];
        rowHeadings_cell= [rowHeadings_cell,'Edr3 (V)'];
    end
end

% table
updateWaitbar(File,'Saving compiled spreadsheet...');
checkTime = tic;
while true
    try
        [status,values] = fileattrib(filePath_xlsx);
        if status
            isWriteable = values.UserWrite;
            if ~isWriteable
                copyfile(filePath_xlsx,tempPath_xlsx,'f');
                xlsxPath = tempPath_xlsx;
            else
                xlsxPath = filePath_xlsx;
            end
        else
            isWriteable = true;
            xlsxPath = filePath_xlsx;
        end
        if groupNum > 1
            colNum = groupNum + 1;
            colLetter = num2ExcelCol(colNum);
            cellPos = [colLetter '1'];
            channelID = channelID_cell(groupNum);
            data_cell = num2cell(data_arr(:,groupNum));
            colTable = [channelID;data_cell];
            writecell(colTable,xlsxPath, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'Sheet','Values', ...
                'AutoFitWidth',hasAutoWidth, ...
                'Range',cellPos);    % save sheet to .xlsx file
        else
            dataTable = array2table( ...
                data_arr, ...
                'VariableNames',string(channelID_cell{groupNum}), ...
                'RowNames',string(rowHeadings_cell));
            writetable(dataTable,xlsxPath, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'Sheet','Values', ...
                'AutoFitWidth',hasAutoWidth, ...
                'WriteRowNames',true);    % save sheet to .xlsx file
        end
        updateWaitbar(File);
        if ~isWriteable
            delete(filePath_xlsx);
            movefile(tempPath_xlsx,filePath_xlsx,'f');
            updateWaitbar(File);
        end
        break;
    catch ME
        if toc(checkTime) > 10
            rethrow(ME);
        end
    end
end
varargout{2} = filePath_xlsx;
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',fileName_xlsx,endTime,unit);	% .xlsx file saved

stim_arr_round = [ ...
    round(amplitude_arr,2); ...
    round(chargePhase_arr,2); ...
    round(chargeInjection_arr,3)];

if hasDischargeDelay
    excrusion_arr_round = [ ...
        round(activeExcursion1_arr,3); ...
        round(activeExcursion2_arr,3)];
else
    excrusion_arr_round = round(activeExcursion1_arr,3);
end

driving_arr_round = [ ...
    round(drivingVoltage1_arr,3); ...
    round(drivingVoltage2_arr,3)];

effectiveCapacitance_round = round(effectiveCapacitance_arr,3);

if hasInterphaseDelay
    if hasDischargeDelay
        access_arr_round = [ ...
            round(accessVoltage1_arr,3); ...
            round(accessVoltage2_arr,3); ...
            round(accessVoltage3_arr,3); ...
            round(accessVoltage4_arr,3); ...
            round(accessResistance1_arr,3); ...
            round(accessResistance2_arr,3); ...
            round(accessResistance3_arr,3); ...
            round(accessResistance4_arr,3)];
    else
        access_arr_round = [ ...
            round(accessVoltage1_arr,3); ...
            round(accessVoltage2_arr,3); ...
            round(accessVoltage3_arr,3); ...
            round(accessResistance1_arr,3); ...
            round(accessResistance2_arr,3); ...
            round(accessResistance3_arr,3)];
    end
else
    if hasDischargeDelay
         access_arr_round = [ ...
            round(accessVoltage1_arr,3); ...
            round(accessVoltage4_arr,3); ...
            round(accessResistance1_arr,3); ...
            round(accessResistance4_arr,3)];
    else
        access_arr_round = [ ...
            round(accessVoltage1_arr,3); ...
            round(accessResistance1_arr,3)];
    end
end

chargingCapacitance_round = round(chargingCapacitance_arr,3);

data_arr_round = [ ...
    stim_arr_round; ...
    excrusion_arr_round; ...
    driving_arr_round; ...
    effectiveCapacitance_round; ...
    access_arr_round; ...
    chargingCapacitance_round];
    
if ~isVoltageSpecial
    data_arr_round = [data_arr_round; ...
        round(activeDriving1_arr,3); ...
        round(activeDriving2_arr,3)];
end
if hasReturnChannel
    data_arr_round = [data_arr_round; ...
        round(returnDriving1_arr,3); ...
        round(returnDriving2_arr,3)];
end
try
    dataTable_round = array2table( ...
        data_arr_round, ...
        'VariableNames',channelID_cell, ...
        'RowNames',rowHeadings_cell);
    disp(dataTable_round);
catch
end
updateWaitbar(File);

end