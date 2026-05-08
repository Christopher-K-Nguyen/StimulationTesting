function [scope,quitProgram] = setAnimalScope()
%% Constants
% Dimensions
% DIMS_DIALOG = [1 70];       % dialog dimensions
% Options

%% Variables
% currentMonScale_V_uA = 1e-3;
confirmResouceSelect = false;
% acqModeAvg = false;
quitProgram = false;
scope = [];

%% Function
% Searching for resources
while confirmResouceSelect == 0
    fprintf('Searching for resources...');
    resource = instrhwinfo('visa','tek'); %#ok<INSTHWV> % search for USB resource
    fprintf('found.\n');                           % USB resource found
    % device list
    resourceName_cell = resource.ObjectConstructorName;  % resource name (cell)
    strPattern1 = "visa(";
    strPattern2 = "tek";
    strPattern3 = ", ";
    strPattern4 = ");";
    strPattern5 = "'";
    resourceName_fix1 = erase(resourceName_cell,strPattern1);
    resourceName_fix2 = erase(resourceName_fix1,strPattern2);
    resourceName_fix3 = erase(resourceName_fix2,strPattern3);
    resourceName_fix4 = erase(resourceName_fix3,strPattern4);
    resourceName_list = erase(resourceName_fix4,strPattern5);
    
    % Dialog box
    fprintf('Selecting resource...');
%     titleListResource = 'Resource Selection';	% list title
%     promptListCh = {...                 % list prompts
%         'Select resource to connect.',...             % insrtuction
%         'Check with TekVISA and MATLAB Instrument Control for correct resource.'};
%     [scopeSelect,scopeSelect_tf] = listdlg(...	% list dialog
%         'PromptString',promptListCh,... % list prompts
%         'ListString',resourceName_list,...    % list
%         'SelectionMode','single',...
%         'Name',titleListResource);           % list title
%     
%     % Collect input
%     if scopeSelect_tf == 0             % cancel detected
%         fprintf('\nQuitting...\n\n'); % quitting
%         quitProgram = 1;
%         return;                     % exit program
%     end
%     
%     % Confirm resource selection
%     resourceName = char(resourceName_list(scopeSelect));
%     titleQuestResource = 'Confirm Resource Selection';
%     % Format questions
%     promptQuestResouceSelect = sprintf('Resource selected:{\\bf%s}',resourceName);
%     % Question box
%     questResouceSelect = questdlg(...               % question dialog
%         promptQuestResouceSelect,...                % question prompts
%         titleQuestResource,...                    	% question title
%         BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
%         opts);                                      % dialog options
%     % Confirmation
%     switch questResouceSelect               % apply choice
%         case BUTTON_CONFIRM                 % check confirmation
%             confirmResouceSelect = 1;       % confirm info
%             fprintf('%s\n',resourceName);   % info confirmed
%         case BUTTON_TRY                     % try again
%             confirmResouceSelect = 0;       % trying again
%             fprintf('\nTrying agin...\n\n');% starting over
%         case BUTTON_CANCEL                  % quit
%             fprintf('\nQuitting...\n\n');   % quitting
%             quitProgram = 1;
%             return;                         % exit program
%         otherwise                           % cancel
%             fprintf('\nQuitting...\n\n');   % quitting
%             quitProgram = 1;
%             return;                         % exit program
%     end
    hasUSB = contains(resourceName_list,'USB','IgnoreCase',true);
    usb_idx = find(hasUSB,1);
    resourceName = char(resourceName_list(usb_idx));
    fprintf('%s\n',resourceName);   % info confirmed
    confirmResouceSelect = 1;       % confirm info
end


% Connecting to oscilloscope
fprintf('Connecting to oscilloscope...');	% connect to oscilloscope
scope = visa('tek',resourceName);           %#ok<VISA> % create VISA object
scope.InputBufferSize = 100000;         % expand data buffer for detailed waveform
fopen(scope);                           % start oscillocope
fprintf('OK.\n');                           % selected resource

% Set up acquisition mode
fprintf('Setting acquisition mode...');
% promptQuestAcqMode = {'Acquire samples or averages?'};
% titleQuestAcqMode = 'Acquisition Mode';
% questAcqMode = questdlg(...                    % question dialog
%     promptQuestAcqMode,...                     % question prompts
%     titleQuestAcqMode,...                           % question litle
%     BUTTON_AVG,BUTTON_SAMPLE,BUTTON_CANCEL,...   % buttons
%     opts);                                          % dialog options
% % Confirmation
% switch questAcqMode                             % apply choice
%     case BUTTON_AVG                             % average mode
        acqModeAvg = true;                         % set to average mode
        fprintf(scope,'ACQuire:MODe AVErage');  % set waveform acquisition to averages
        fprintf('AVERAGE.\n');
%     case BUTTON_SAMPLE                          % sample mode
%         fprintf(scope,'ACQuire:MODe SAMple');   % set waveform acquisition to samples
%         fprintf('SAMPLE.\n');
%     case BUTTON_CANCEL                    % quit
%         fprintf('\nQuitting...\n');     % quitting
%         quitProgram = true;
%         return;                         % exit program
%     otherwise                           % cancel
%         fprintf('\nQuitting...\n');     % quitting
%         quitProgram = true;
%         return;                         % exit program
% end

% Average Mode
if acqModeAvg == true
    fprintf('Setting average acquisition mode...');
%     while confirmAcqModeAvg == 0
%         % Number of samples list
%         promptQuestListNum = string(listNum);
%         promptQuestList_str = cellstr(promptQuestListNum);
%     
%         % Dialog box
%         titleListCh = 'Number of Samples';            % list title
%         promptListCh = {'Select number of samples to average.'};% list prompt
%         [numOfSamples_select,numOfSamples_tf] = listdlg(...% list dialog
%             'PromptString',promptListCh,...         % list prompt
%             'ListString',promptQuestList_str,...    % list
%             'SelectionMode','single',...            % single choice
%             'InitialValue',4,...                    % default selection
%             'Name',titleListCh,...                  % list title
%             'ListSize',[200 100]);                    
%     
%         % Collect input
%         if numOfSamples_tf == false   	% cancel detected
%             fprintf('\nQuitting...\n\n'); % quitting
%             quitProgram = true;
%             return;                     % exit program
%         end
%     
%         % Confirm number of samples selection
%         titleQuestListNum = 'Confirm Number of Samples';
%         % Format questions
%         numOfSamples = listNum(numOfSamples_select);
%         numOfSamples_use = sprintf('Number of samples: {\\bf%d}',numOfSamples);
%         promptQuestListNum = cellstr(numOfSamples_use);
%         % Question box
%         questListNum = questdlg(...                      % question dialog
%             promptQuestListNum,...                       % question prompts
%             titleQuestListNum,...                        % question title
%             BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
%             opts);                                      % dialog options
%         % Confirmation
%         switch questListNum                              % apply choice
%             case BUTTON_CONFIRM                         % check confirmation
                numOfSamples = 256;
                numOfSamples_str = sprintf('ACQuire:NUMAvg %d',numOfSamples);
                confirmAcqModeAvg = true;
                fprintf(scope,numOfSamples_str);    % set number of wavforms for average
                fprintf('%d samples.\n',numOfSamples);  % info confirmed
%             case BUTTON_TRY                             % try again
%                 fprintf('\nTrying agin...\n');          % starting over
%             case BUTTON_CANCEL                          % quit
%                 fprintf('\nQuitting...\n');             % quitting
%                 quitProgram = true;
%                 return;                                 % exit program
%             otherwise                                   % cancel
%                 fprintf('\nQuitting...\n');             % quitting
%                 quitProgram = true;
%                 return;                                 % exit program
%         end
%     end
end

fprintf(scope,'DATa:ENCdg ASCii');  % ensure ASCII encoding for sensible data output
fprintf(scope,'ACQuire:STAte RUN');

% Coupling
fprintf('Setting CH1 coupling...');
fprintf(scope,'CH1:COUPling DC');	% coupling on voltage
fprintf('DC.\n');
fprintf('Setting CH2 coupling...');
fprintf(scope,'CH2:COUPling DC');	% coupling on current
fprintf('DC.\n');

% Horizontal setup
fprintf('Setting horizontal scale...');
isHorizScaleGood = false;
while ~isHorizScaleGood
    fprintf(scope,'HORizontal:MAIn:SCAle 100E-6');    % 100 us/div horizontal scale
    fprintf(scope,'HORizontal:MAIN:SCAle?');
    horizScale = str2num(fscanf(scope));
    if horizScale == 100e-6
        isHorizScaleGood = true;
        fprintf('%.2e \n',horizScale);
    end
end
fprintf('Setting horizontal position...');
isHorizPosGood = false;
while ~isHorizPosGood
    fprintf(scope,'HORizontal:MAIN:POSition 300E-6'); % 300 us horizontal position
    fprintf(scope,'HORizontal:MAIN:POSition?');
    horizPos = str2num(fscanf(scope));
    if horizPos == 300e-6
        isHorizPosGood = true;
        fprintf('%.2e \n',horizPos);
    end
end

% Vertical setup
% CH1
% unit
fprintf('Setting CH1 unit...');
fprintf(scope,'CH1:YUNit "V"');
fprintf('"V"\n');
% probe
fprintf('Setting CH1 probe...');
fprintf(scope,'CH1:PRObe 1');
fprintf('1X\n');
% position
fprintf('Setting CH1 position...');
fprintf(scope,'CH1:POSition 0');
fprintf('0 V\n');
% scale
fprintf('Setting CH1 scale...');
fprintf(scope,'CH1:SCAle 500E-3');  % 500 mV/div vertical scale for voltage
fprintf('500 mV\n');
% CH2
% unit
fprintf('Setting CH2 unit...');
fprintf(scope,'CH2:YUNit "A"');
fprintf('"A"\n');
% probe
fprintf('Setting CH1 probe...');
fprintf(scope,'CH2:PROBe 1');
fprintf('1X\n');
% position
fprintf('Setting CH2 position...');
fprintf(scope,'CH2:POSition 0');
fprintf('0 A\n');
% scale
fprintf('Setting CH2 scale...');
% fprintf(scope,'CH2:SCAle 100-3');   % 100 mV/div vertical scale for current
source = 'CH2';
scale = 100e-3;
scale_use = sprintf('%s:SCAle %g',source,scale);
fprintf(scope,scale_use);
fprintf('100 mA\n');

% Cursor setup
fprintf('Opening cursors...');
fprintf(scope,'CURSor:FUNCtion VBArs');
fprintf('OK.\n');
% cursor 1
fprintf('Setting Cursor 1 position...');
fprintf(scope,'CURSor:FUNCtion VBArs');
fprintf(scope,'CURSor:SELect:SOUrce CH1');
pulseWidth = 200e-6;
verticalBar1 = sprintf('CURSor:VBArs:POSITION1 %.2e',pulseWidth);
fprintf(scope,verticalBar1);
fprintf('%.2e s\n',pulseWidth);
% cursor 2
fprintf('Setting Cursor 2 position...');
maxPotential_time = 212e-6;
verticalBar2 = sprintf('CURSor:VBArs:POSITION2 %.2e',maxPotential_time);
fprintf(scope,verticalBar2);
fprintf('%.2e s\n',maxPotential_time);


% Trigger setup
fprintf('Setting trigger...');
fprintf(scope,'TRIGger:MODe NORMal');                   % trigger mode on normal
fprintf('normal...');
fprintf(scope,'TRIGger:MAIn:EDGe:SOUrce CH2');          % trigger source on current
fprintf('CH2...');
fprintf(scope,'TRIGger:MAIn:EDGE:COUPling DC');         % trigger on DC coupling
fprintf('DC...');

triggerLevel = 20 * 0.9;
triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %.2e',triggerLevel);
fprintf(scope,triggerLevel_text);
fprintf(scope,'TRIGger:MAIn:EDGe:SLOpe FALl');  % trigger on falling slope
fprintf('falling edge...');
% fprintf('OK.\n');
% fprintf('Trigger Level: %.2e A\n',triggerLevel);
fprintf('%.2e A\n',triggerLevel);

end