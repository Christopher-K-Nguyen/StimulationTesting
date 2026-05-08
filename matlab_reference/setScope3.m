function [File,quitProgram] = setScope3(File)
%% Constants
MAX_CONNECT_ATTEMPTS = 2;
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_CANCEL = 'Cancel';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';

%% Variables
stimType = File.Parameters.Type;
isMultiTest = contains2(stimType,'MULTI');
% currentMonScale_V_uA = 1e-3;
confirmResouceSelect = false;
quitProgram = false;

% Structure
Trigger = struct( ...
    'Source','', ...
    'Monitor','');
Info = struct( ...
    'Channel','CH1', ...
    'Coupling','DC', ...
    'VerticalScale',0, ...
    'HorizontalScale',0, ...
    'DataLength',2500, ...
    'AcquisitionMode','Average mode');
Settings = struct( ...
    'DataWidth',[], ...
    'Bits',[], ...
    'DataEncoding','BIN', ...
    'BinaryFormat',2, ...
    'BinaryOrder','MSB', ...
    'DataLength',2500, ...
    'Info',Info, ...
    'ReferenceFormat','Y', ...
    'Interval',0, ...
    'TriggerOffset',0, ...
    'HorizontalStart',0, ...
    'HorizontalUnit','s', ...
    'VerticalScaleFactor',0, ...
    'WaveformConversion',0, ...
    'VerticalPosition',0, ...
    'VerticalUnit','V', ...
    'Time',[]);
Oscilloscope = struct( ...
    'Make','', ...
    'Model','', ...
    'Serial','', ...
    'Firmware','', ...
    'Resource','', ...
    'Info',[], ...
    'Trigger',Trigger, ...
    'Settings',Settings, ...
    'Object',[]);
make = '';
model = '';
serial = '';
firmwareVersion = '';

%% Function
confirmScope = false;
connectCount = 0;
while ~confirmScope
    % Searching for resources
    while ~confirmResouceSelect
        fprintf('Searching for resources...');
        startTime = tic;
        instrreset;
        try
            resource = instrhwinfo('visa','tek'); %#ok<INSTHWV> % search for USB resource
            type = 'tek';
        catch
            resource = instrhwinfo('visa','ni'); %#ok<INSTHWV> % search for USB resource
            type = 'ni';
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('found (%.2f %s)\n',endTime,unit);                           % USB resource found
        % device list
        resourceName_cell = resource.ObjectConstructorName;  % resource name (cell)
        usb_tf = contains(resourceName_cell,'USB','IgnoreCase',true);
        usb_idx = find(usb_tf);
        isUSB = any(usb_idx);
        if isUSB
            resourceName_alt = resourceName_cell{usb_idx};
        end
        strPattern = {'visa(',type,', ',');',''''};
        resourceName_list = erase(resourceName_cell,strPattern);
        empty_idx = cellfun(@isempty,resourceName_list); % identify the empty cells
        resourceName_list(empty_idx) = [];
        numOfResources = length(resourceName_list);

        % Dialog box
        fprintf('\tSelecting resource...');
        startTime = tic;
%         if numOfResources > 1
%             promptListCh = {...                 % list prompts
%                 'Select resource to connect.', ...             % insrtuction
%                 'Check with NI VISA and MATLAB Instrument Control for correct resource.'};
%             [scopeSelect,~] = listdlg( ...	% list dialog
%                 'PromptString',promptListCh, ... % list prompts
%                 'ListString',resourceName_list, ...    % list
%                 'Name','Resource Selection', ...
%                 'SelectionMode','single', ...
%                 'ListSize',[100 200]);           % list title
% 
%             % Collect input
%             if isempty(scopeSelect)             % cancel detected
%                 fprintf('\nQuitting...\n\n'); % quitting
%                 quitProgram = true;
%                 return;                     % exit program
%             end
% 
%             % Confirm resource selection
%             resourceName = resourceName_list{scopeSelect};
%             % Format questions
%             promptQuestResouceSelect = sprintf('Resource selected:{\\bf%s}',resourceName);
%             % Question box
%             questResouceSelect = questdlg( ...               % question dialog
%                 promptQuestResouceSelect, ...                % question prompts
%                 'Confirm Resource Selection', ...                    	% question title
%                 BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL, ... % buttons
%                 opts);                                      % dialog options
%             % Confirmation
%             switch questResouceSelect               % apply choice
%                 case BUTTON_CONFIRM                 % check confirmation
%                     confirmResouceSelect = true;       % confirm info
%                     fprintf('%s\n',resourceName);   % info confirmed
%                 case BUTTON_TRY                     % try again
%                     confirmResouceSelect = false;       % trying again
%                     fprintf('\nTrying agin...\n\n');% starting over
%                 case BUTTON_CANCEL                  % quit
%                     fprintf('\nQuitting...\n\n');   % quitting
%                     quitProgram = 1;
%                     break;                         % exit program
%                 otherwise                           % cancel
%                     fprintf('\nQuitting...\n\n');   % quitting
%                     quitProgram = true;
%                     break;                         % exit program
%             end
%         end
        if isUSB
            baudRate = 9600;
            resourceName = resourceName_list{usb_idx};
            terminator = 'LF';
%             oscilloscope = visa(type,resourceName);
            oscilloscope = eval(resourceName_alt);
        else
            baudRate = 19200;
            %         try
            %             resourceName = resourceName_list{numOfResources};
            %             oscilloscope = visa(type,resourceName); %#ok<*TNMLP,*VISA>
            %         catch
            resourceNum_arr = flip(1:numOfResources);
            for resourceNum = resourceNum_arr
                resourceName = resourceName_list{resourceNum};
                oscilloscope = visa(type,resourceName); %#ok<*TNMLP,*VISA>
%                 try
%                     fopen(oscilloscope);
%                 catch
%                 end
%                 status = oscilloscope.Status;
%                 if contains2(status,'open')
%                     fclose(oscilloscope);
%                     break;
%                 end
                setScopeStatus(oscilloscope,'open');
            end
            %         end
            terminator = 'LF';
        end
        [endTime,unit] = getEndTime(startTime);
        fprintf('%s (%.2f %s)\n',resourceName,endTime,unit);   % info confirmed
        confirmResouceSelect = true;       % confirm info
    end

    % Connecting to oscilloscope
    fprintf('\tConnecting to oscilloscope...');	% connect to oscilloscope
    startTime = tic;
    connectCount = connectCount + 1;
    % if isUSB
    bufferSize = 20e3;
    % bufferSize_use = addCommas(bufferSize);
    oscilloscope.InputBufferSize = bufferSize;         % expand data buffer for detailed waveform
    ENDIAN_LITTLE = 'littleEndian';
    ENDIAN_BIG = 'bigEndian';
    byteOrder = ENDIAN_BIG;
    oscilloscope.ByteOrder = byteOrder;
    timeout = 10;
    oscilloscope.Timeout = timeout;
    % fprintf('buffer size = %s bytes...',bufferSize_use);
    if ~isUSB
        oscilloscope.BaudRate = baudRate;
        oscilloscope.FlowControl = 'hardware';
        oscilloscope.Terminator = terminator;
    end
    fopen(oscilloscope);                           % start oscillocope
    % else
    %     resource = instrfind('Type','gpib');
    %     boardIdx = resource.BoardIndex;
    %     primaryAddress = resource.PrimaryAddress;
    %     oscilloscope = gpib('ni',boardIdx,primaryAddress);
    %     resourceName = oscilloscope.Name;

    %     oscilloscope = serialport(resourceName,baudRate);
    %     configureTerminator(oscilloscope,'CR/LF');
    %     oscilloscope.FlowControl = 'hardware';
    %     oscilloscope.Timeout = 300;
    %     scopeID = writeread(oscilloscope,'*IDN?');
    % end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);                           % selected resource

    fprintf('\tGetting information...');
    startTime = tic;
    id_char = '';
%     while isempty(id_char)
        try
            fprintf(oscilloscope,'*IDN?');
            %         pause(1);
            id_char = fgetl2(oscilloscope);
        catch
            id_char = '';
        end
        if ~isempty(id_char)
            id_cell = strsplit(id_char,',');
            numOfID = length(id_cell);
            make = id_cell{1};
            model = id_cell{2};
            switch numOfID
                case 4
                    serial = id_cell{3};
                    firmware = strsplit(id_cell{4});
                    firmwareVersion = firmware{2};
                case 3
                    firmware = strsplit(id_cell{3});
                    firmwareVersion = firmware{2};
            end
            confirmScope = true;
            [endTime,unit] = getEndTime(startTime);
            fprintf('%s %s (%.2f %s)\n',make,model,endTime,unit);
        else
            fprintf('FAILED\n');
%             fclose(oscilloscope);
            confirmResouceSelect = false;
            if connectCount > MAX_CONNECT_ATTEMPTS
                break;
            else
                pause(1);
                continue;
            end
        end
%     end

%     if isMultiTest
%         isTPS = contains2(model,'TPS');
%         if ~isTPS
%             prompt = 'ERROR: Must use TPS2000B Series oscilloscope for Multipolar Test!';
%             disp(prompt);
%             msg = sprintf('\\bf%s',prompt);
%             msgBox = msgbox(msg);
%             waitfor(msgBox);
%         end
%     else
%         
%     end

end
if connectCount > MAX_CONNECT_ATTEMPTS
    quitProgram = true;
    return;
end

if ~isUSB
    fprintf('Setting up RS232 communication...');
    startTime = tic;

    baudRate_check = 0;
    baudRate_use = sprintf('RS232:BAUd %d',baudRate);
    while baudRate_check ~= baudRate
        fprintf(oscilloscope,baudRate_use);
        fprintf(oscilloscope,'RS232:BAUd?');
        baudRate_raw = fgetl2(oscilloscope);
        baudRate_check = str2double(baudRate_raw);
    end

    hardFlag = 'ON';
    hardFlag_check = '';
    hardFlag_use = sprintf('RS232:HARDFlagging %s',hardFlag);
    while ~contains2(hardFlag_check,hardFlag)
        fprintf(oscilloscope,hardFlag_use);
        fprintf(oscilloscope,'RS232:HARDFlagging?');
        hardFlag_check = fgetl2(oscilloscope);
    end

    terminator_check = '';
    terminator_use = sprintf('RS232:TRANsmit:TERMinator %s',terminator);
    while ~contains2(terminator_check,terminator)
        fprintf(oscilloscope,terminator_use);
        fprintf(oscilloscope,'RS232:TERMinator?');
        terminator_check = fgetl2(oscilloscope);
    end

    parity = 'NONe';
    parity_check = '';
    parity_use = sprintf('RS232:PARity %',parity);
    while ~contains2(parity_check,parity)
        fprintf(oscilloscope,parity_use);
        fprintf(oscilloscope,'RS232:PARity?');
        parity_check = fgetl2(oscilloscope);
    end

    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);
end

% sourcePrompt = 'Enter 0 for CH2 as trigger source, otherwise 1 for EXT: ';
% isExt = input(sourcePrompt);
% if isExt
%     monitorPrompt = 'Enter "CH3" or "CH4" as trigger monitor, otherwise leave empty: ';
%     inputMonitor = input(monitorPrompt,'s');
%     if contains2(inputMonitor,'3')
%         trigMonitor = 'CH3';
%     elseif contains2(inputMonitor,'4')
%         trigMonitor = 'CH4';
%     else
%         trigMonitor = '';
%     end
% end
isExt = true;
trigMonitor = '';

% Set up acquisition mode
fprintf('Setting acquisition mode...');
startTime = tic;
%  phaseWidth = File.Parameters.PhaseWidth1;
% if phaseWidth >= 100
fprintf(oscilloscope,'ACQuire:MODe AVErage');  % set waveform acquisition to averages
[endTime,unit] = getEndTime(startTime);
fprintf('AVERAGE (%.2f %s)\n',endTime,unit);

% Average Mode
fprintf('Setting average acquisition mode...');
startTime = tic;
numOfSamples = 128;
numOfSamples_use = sprintf('ACQuire:NUMAvg %d',numOfSamples);
fprintf(oscilloscope,numOfSamples_use);    % set number of wavforms for average
[endTime,unit] = getEndTime(startTime);
fprintf('%d samples (%.2f %s)\n',numOfSamples,endTime,unit);  % info confirmed
% else
%      fprintf(oscilloscope,'ACQuire:MODe SAMple');  % set waveform acquisition to samples
%      [endTime,unit] = getEndTime(startTime);
%      fprintf('SAMPLE (%.2f %s)\n',endTime,unit);
% end
fprintf(oscilloscope,'ACQuire:STAte RUN');

% Headers
fprintf('Setting headers...');
startTime = tic;
fprintf(oscilloscope,'HEADer OFF');
[endTime,unit] = getEndTime(startTime);
fprintf('OFF (%.2f %s)\n',endTime,unit);

% Data Encoding
fprintf('Setting data encoding...');
startTime = tic;
% ASCII = 'ASCii';
RIBIN = 'RIBinary';
% RPBIN = 'RPBinary';
SRIBIN = 'SRIbinary';
% SRPBIN = 'SRPbinary';
switch byteOrder
    case ENDIAN_BIG
        binOrder = 'MSB';
        binaryEncoding = RIBIN;
    case ENDIAN_LITTLE
        binOrder = 'LSB';
        binaryEncoding = SRIBIN;
end
dataEncoding = binaryEncoding;
dataEncoding_use = sprintf('DATa:ENCdg %s',dataEncoding);
fprintf(oscilloscope,dataEncoding_use);  % encoding for sensible data output
[endTime,unit] = getEndTime(startTime);
fprintf('%s (%.2f %s)\n',dataEncoding,endTime,unit);

% Binary Order
if contains2(dataEncoding,'BIN')
    fprintf('Setting binary order...');
    startTime = tic;
    binOrder_check = '';
    binOrder_use = sprintf('WFMPre:BYT_Or %s',binOrder);
    while ~contains2(binOrder,binOrder_check)
        fprintf(oscilloscope,binOrder_use);
        fprintf(oscilloscope,'WFMPre:BYT_Or?');
        binOrder_check = fgetl2(oscilloscope);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',binOrder_check,endTime,unit);
end

% Data Width
fprintf('Setting data width...');
startTime = tic;
% dataWidth = 1;
dataWidth_check = 0;
fprintf(oscilloscope,'DATa:ENCdg?');
dataEncoding = fgetl2(oscilloscope);
if contains2(dataEncoding,'ASC')
    dataWidth = 1;
else
    if isUSB
        dataWidth = 2;
    else
        dataWidth = 1;
    end
end
while dataWidth_check ~= dataWidth
    dataWidth_use = sprintf('DATa:WIDth %d',dataWidth);
    fprintf(oscilloscope,dataWidth_use);
    fprintf(oscilloscope,'DATa:WIDth?');
    dataWidth_raw = fgetl2(oscilloscope);
    dataWidth_check = str2double(dataWidth_raw);
end
[endTime,unit] = getEndTime(startTime);
fprintf('%d byte (%.2f %s)\n',dataWidth,endTime,unit);

% Display
fprintf('Resetting channels for display...');
startTime = tic;
fprintf(oscilloscope,'SELect:CH1 OFF');
fprintf(oscilloscope,'SELect:CH2 OFF');
fprintf(oscilloscope,'SELect:CH3 OFF');
fprintf(oscilloscope,'SELect:CH4 OFF');
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit)

% Vertical setup
if isMultiTest
    numOfChannels = 4;
else
    numOfChannels = 2;
end
channel_cell = {'CH1','CH2','CH3','CH4'};
for channel_idx = 1:numOfChannels
    scopeChannel = channel_cell{channel_idx};

    % Display
    fprintf('Setting %s for display...',scopeChannel);
    startTime = tic;
    status = 'ON';
    status_check = '';
    status_use = sprintf('SELect:%s ON',scopeChannel);
    while ~contains2(status_check,status)
        fprintf(oscilloscope,status_use);
        fprintf(oscilloscope,'SELect?');
        status_raw = fgetl2(oscilloscope);
        status_cell = strsplit(status_raw,';');
        status_channel = status_cell{channel_idx};
        if contains2(status_channel,'1')
            status_check = 'ON';
        else
            status_check = 'OFF';
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',status,endTime,unit)

    % Coupling
    fprintf('Setting %s coupling...',scopeChannel);
    startTime = tic;
    coupling = 'DC';
    coupling_check = '';
    coupling_use = sprintf('%s:COUPling %s',scopeChannel,coupling);
    coupling_query = sprintf('%s:COUPling?',scopeChannel);
    while ~contains2(coupling_check,coupling)
        fprintf(oscilloscope,coupling_use);	% coupling on voltage
        fprintf(oscilloscope,coupling_query);
        coupling_check = fgetl2(oscilloscope);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',coupling,endTime,unit);

    % Unit
    fprintf('Setting %s unit...',scopeChannel);
    startTime = tic;
    channelUnit = 'V';
    channelUnit_check = '';
    channelUnit_use = sprintf('%s:YUNit "%s"',scopeChannel,channelUnit);
    channelUnit_query = sprintf('%s:YUNit?',scopeChannel);
    while ~contains2(channelUnit_check,channelUnit)
        fprintf(oscilloscope,channelUnit_use);
        fprintf(oscilloscope,channelUnit_query);
        channelUnit_check = fgetl2(oscilloscope);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('"%s" (%.2f %s)\n',channelUnit,endTime,unit);

    % Probe
    fprintf('Setting %s probe...',scopeChannel);
    startTime = tic;
    probe = 1;
    probe_check = 0;
    switch channelUnit
        case 'A'
            probeType = 'CURRENTPROBe';
        case 'V'
            probeType = 'PRObe';
    end
    probe_use = sprintf('%s:%s 1',scopeChannel,probeType);
    probe_query = sprintf('%s:%s?',scopeChannel,probeType);
    while probe_check ~= probe
        fprintf(oscilloscope,probe_use);
        fprintf(oscilloscope,probe_query);
        probe_raw = fgetl2(oscilloscope);
        probe_check = str2double(probe_raw);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%dX (%.2f %s)\n',probe,endTime,unit);

    % Polarity
    fprintf('Setting %s polarity...',scopeChannel);
    startTime = tic;
    invert = 'OFF';
    invert_check = '';
    invert_use = sprintf('%s:INVert OFF',scopeChannel);
    invert_query = sprintf('%s:INVert?',scopeChannel);
    while ~contains2(invert_check,invert)
        fprintf(oscilloscope,invert_use);
        fprintf(oscilloscope,invert_query);
        invert_check = fgetl2(oscilloscope);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',invert,endTime,unit);

    % Position
    fprintf('Setting %s position...',scopeChannel);
    startTime = tic;
    position = 0;
    position_check = [];
    position_use = sprintf('%s:POSition %.2e',scopeChannel,position);
    position_query = sprintf('%s:POSition',scopeChannel);
    while position_check ~= position
        fprintf(oscilloscope,position_use);
        fprintf(oscilloscope,position_query);
        position_raw = fgetl2(oscillosocpe);
        position_check = str2double(position_raw);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e %s (%.2f %s)\n',position,channelUnit,endTime,unit);

    % Scale
    fprintf('Setting %s scale...',scopeChannel);
    startTime = tic;
    if contains2(scopeChannel,'2')
        scale = 100e-3;
    else
        scale = 1;
    end
    scale_check = 0;
    scale_use = sprintf('%s:SCAle %.2e',scopeChannel,scale);
    scale_query = sprintf('%s:SCAle?',scopeChannel);
    while scale_check ~= scale
        fprintf(oscilloscope,scale_use);  % V/div vertical scale for voltage
        fprintf(oscilloscope,scale_query);
        scale_raw = fgetl2(oscilloscope);
        scale_check = str2double(scale_raw);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e %s (%.2f %s)\n',scale,channelUnit,endTime,unit);
end
% [endTime,unit] = getEndTime(startTime);
% fprintf('1X (%.2f %s)\n',endTime,unit);
% % polarity
% fprintf('Setting CH2 polarity...');
% startTime = tic;
% fprintf(oscilloscope,'CH2:INVert OFF');
% [endTime,unit] = getEndTime(startTime);
% fprintf('OFF (%.2f %s)\n',endTime,unit);
% % position
% fprintf('Setting CH2 position...');
% startTime = tic;
% fprintf(oscilloscope,'CH2:POSition 0');
% [endTime,unit] = getEndTime(startTime);
% fprintf('0 %s (%.2f %s)\n',unitCH2,endTime,unit);
% % scale
% fprintf('Setting CH2 scale...');
% startTime = tic;
% % fprintf(oscilloscope,'CH2:SCAle 100-3');   % 100 mV/div vertical scale for current
% source = 'CH2';
% scale = 100e-3;
% scale_use = sprintf('%s:SCAle %.2e',source,scale);
% fprintf(oscilloscope,scale_use);
% [endTime,unit] = getEndTime(startTime);
% fprintf('100 m%s (%.2f %s)\n',unitCH2,endTime,unit);

% Trigger setup
fprintf('Setting trigger...');
startTime = tic;
fprintf(oscilloscope,'TRIGger:MODe NORMal');                   % trigger mode on normal
fprintf('normal...');
if isExt
    trigSource = 'EXT';
    if ~isempty(trigMonitor)
        monitorSelect = sprintf('SELect:%s ON',trigMonitor);
        fprintf(oscilloscope,monitorSelect);
        monitorCoupling = sprintf('%s:COUPling DC',trigMonitor);
        fprintf(oscilloscope,monitorCoupling);	% coupling on voltage
        monitorProbe = sprintf('%s:PRObe 10',trigMonitor);
        fprintf(oscilloscope,monitorProbe);
        monitorInvert = sprintf('%s:INVert OFF',trigMonitor);
        fprintf(oscilloscope,monitorInvert);
        monitorScale = sprintf('%s:SCAle 2',trigMonitor);
        fprintf(oscilloscope,monitorScale);
        monitorPosition = sprintf('%s:POSition 0',trigMonitor);
        fprintf(oscilloscope,monitorPosition);
    end
    slope = 'RISe';
    edge = 'rising';
end
trigSource_use = sprintf('TRIGger:MAIn:EDGe:SOUrce %s',trigSource);
fprintf(oscilloscope,trigSource_use);          % trigger source
fprintf('%s...',trigSource);
fprintf(oscilloscope,'TRIGger:MAIn:EDGE:COUPling DC');         % trigger on DC coupling
fprintf('DC...');
slope_use = sprintf('TRIGger:MAIn:EDGe:SLOpe %s',slope);
fprintf(oscilloscope,slope_use);  % trigger on rising slope
edge_use = sprintf('%s edge',edge);
fprintf(edge_use);
[endTime,unit] = getEndTime(startTime);
fprintf(' (%.2f %s)\n',endTime,unit);

Oscilloscope.Make = make;
Oscilloscope.Model = model;
if ~isempty(serial)
    Oscilloscope.Serial = serial;
else
    Oscilloscope = rmfield(Oscilloscope,'Serial');
end
Oscilloscope.Firmware = firmwareVersion;
Oscilloscope.Resource = resourceName;  % resource name
instr = instrhwinfo(oscilloscope);
Oscilloscope.Info = instr;
Oscilloscope.Trigger.Source = trigSource;
Oscilloscope.Trigger.Monitor = trigMonitor;
Oscilloscope.Object = oscilloscope;            % object
File.Oscilloscope = Oscilloscope;      % oscilloscope

% Default View
amplitude1 = -20;
setDefaultScopeView3(File,amplitude1);

%Settings
File = getSettings(File,[]);
% File = getSettings(File,'time');

end